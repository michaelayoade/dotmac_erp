-- =============================================================================
-- Bulk GL Posting Backfill for AR Invoices
-- =============================================================================
-- Creates journal entries + posted ledger lines for all unposted AR invoices
-- in a single transaction. Handles:
--   - Single-line and multi-line invoices
--   - Zero-amount line skipping
--   - Negative (discount) line flipping
--   - Credit notes (reversed debit/credit)
--   - Separate tax posting (when tax account mapping exists)
--   - Header/line delta allocation (last revenue line absorbs remainder)
--
-- Runtime: ~2-5 minutes for ~120K invoices (vs 5+ hours via Python ORM)
-- =============================================================================

-- Constants
\set org_id     '''00000000-0000-0000-0000-000000000001'''
\set user_id    '''00000000-0000-0000-0000-000000000000'''
\set tax_code   '''4b180259-b0b0-41fb-955b-0e089df66b42'''
\set tax_acct   '''d6fcaecf-e1b7-4dce-9743-368eb5b1775c'''
\set tax_acct_code '''2000'''

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- Step 0: Clean up orphaned journals from previous crashed Python runs
-- ─────────────────────────────────────────────────────────────────────────────
DELETE FROM gl.journal_entry_line
WHERE journal_entry_id IN (
    SELECT je.journal_entry_id FROM gl.journal_entry je
    WHERE je.source_module = 'AR' AND je.source_document_type = 'INVOICE'
      AND je.status = 'APPROVED'
      AND NOT EXISTS (
        SELECT 1 FROM gl.posted_ledger_line pll
        WHERE pll.journal_entry_id = je.journal_entry_id
      )
);

DELETE FROM gl.journal_entry
WHERE source_module = 'AR' AND source_document_type = 'INVOICE'
  AND status = 'APPROVED'
  AND NOT EXISTS (
    SELECT 1 FROM gl.posted_ledger_line pll
    WHERE pll.journal_entry_id = journal_entry_id
  );

-- ─────────────────────────────────────────────────────────────────────────────
-- Step 1: Build invoice staging table with pre-generated UUIDs
-- ─────────────────────────────────────────────────────────────────────────────
DROP TABLE IF EXISTS _inv_stage;
CREATE TEMP TABLE _inv_stage AS
SELECT
    i.invoice_id,
    i.invoice_number,
    i.invoice_date,
    i.total_amount,
    i.currency_code,
    COALESCE(i.exchange_rate, 1.0) AS exchange_rate,
    i.ar_control_account_id,
    i.correlation_id,
    i.invoice_type,
    fp.fiscal_period_id,
    c.legal_name AS customer_name,
    a_ctrl.account_code AS ar_control_code,
    gen_random_uuid() AS journal_entry_id,
    gen_random_uuid() AS posting_batch_id,
    EXTRACT(YEAR FROM i.invoice_date)::int AS posting_year,
    -- Journal number: JE{YYYYMM}-{seq}
    -- seq assigned in next step
    0::bigint AS seq_num
FROM ar.invoice i
JOIN gl.fiscal_period fp
    ON fp.organization_id = i.organization_id
    AND i.invoice_date BETWEEN fp.start_date AND fp.end_date
JOIN ar.customer c ON c.customer_id = i.customer_id
JOIN gl.account a_ctrl ON a_ctrl.account_id = i.ar_control_account_id
WHERE i.journal_entry_id IS NULL
  AND i.total_amount != 0
  AND i.organization_id = :org_id::uuid;

-- Assign sequential journal numbers starting from current max
WITH max_je AS (
    SELECT COALESCE(MAX(CAST(SUBSTRING(journal_number FROM '[0-9]+$') AS bigint)), 0) AS max_num
    FROM gl.journal_entry
    WHERE organization_id = :org_id::uuid
),
numbered AS (
    SELECT invoice_id, ROW_NUMBER() OVER (ORDER BY invoice_date, invoice_id) AS rn
    FROM _inv_stage
)
UPDATE _inv_stage s
SET seq_num = n.rn + m.max_num
FROM numbered n, max_je m
WHERE s.invoice_id = n.invoice_id;

-- Add journal_number column
ALTER TABLE _inv_stage ADD COLUMN journal_number varchar(30);
UPDATE _inv_stage
SET journal_number = 'JE' || TO_CHAR(invoice_date, 'YYYYMM') || '-' || seq_num::text;

CREATE INDEX ON _inv_stage (invoice_id);

\echo 'Step 1 complete: Invoice staging built'
SELECT COUNT(*) AS invoices_to_post FROM _inv_stage;

-- ─────────────────────────────────────────────────────────────────────────────
-- Step 2: Build line staging with computed revenue/tax amounts
-- ─────────────────────────────────────────────────────────────────────────────
DROP TABLE IF EXISTS _line_stage;
CREATE TEMP TABLE _line_stage AS
SELECT
    il.line_id,
    il.invoice_id,
    il.line_number,
    il.description AS line_description,
    il.line_amount,
    COALESCE(il.tax_amount, 0) AS tax_amount,
    il.revenue_account_id,
    il.tax_code_id,
    il.cost_center_id,
    il.project_id,
    il.segment_id,
    -- Does this line have a separate tax posting?
    CASE
        WHEN il.tax_code_id = :tax_code::uuid
             AND COALESCE(il.tax_amount, 0) != 0
        THEN true
        ELSE false
    END AS has_separate_tax,
    -- Raw revenue total: line_amount + (tax if rolled into revenue)
    il.line_amount + CASE
        WHEN il.tax_code_id = :tax_code::uuid
             AND COALESCE(il.tax_amount, 0) != 0
        THEN 0  -- Tax goes to separate account
        ELSE COALESCE(il.tax_amount, 0)  -- Tax rolled into revenue
    END AS raw_revenue,
    -- Pre-generate UUIDs for journal lines
    gen_random_uuid() AS revenue_line_id,
    gen_random_uuid() AS tax_line_id
FROM ar.invoice_line il
JOIN _inv_stage s ON s.invoice_id = il.invoice_id;

CREATE INDEX ON _line_stage (invoice_id);

-- Mark non-zero revenue lines and identify the "last" one per invoice
-- (Last non-zero revenue line absorbs the header/line delta)
ALTER TABLE _line_stage ADD COLUMN is_zero boolean DEFAULT false;
ALTER TABLE _line_stage ADD COLUMN is_last_nonzero boolean DEFAULT false;

UPDATE _line_stage SET is_zero = true WHERE raw_revenue = 0;

-- Find the last non-zero revenue line per invoice (highest line_number)
WITH last_lines AS (
    SELECT DISTINCT ON (invoice_id) line_id
    FROM _line_stage
    WHERE NOT is_zero
    ORDER BY invoice_id, line_number DESC
)
UPDATE _line_stage l
SET is_last_nonzero = true
FROM last_lines ll
WHERE l.line_id = ll.line_id;

-- Compute adjusted revenue for last line: absorb delta so journal balances
-- delta = total_amount - sum_of_non_last_revenue_lines - sum_of_separate_tax_lines
ALTER TABLE _line_stage ADD COLUMN adjusted_revenue numeric;

WITH inv_sums AS (
    SELECT
        l.invoice_id,
        -- Sum of revenue from all non-zero, non-last lines
        COALESCE(SUM(CASE WHEN NOT l.is_zero AND NOT l.is_last_nonzero
                          THEN l.raw_revenue ELSE 0 END), 0) AS other_revenue,
        -- Sum of separate tax amounts
        COALESCE(SUM(CASE WHEN l.has_separate_tax THEN l.tax_amount ELSE 0 END), 0) AS total_sep_tax
    FROM _line_stage l
    GROUP BY l.invoice_id
)
UPDATE _line_stage l
SET adjusted_revenue = CASE
    -- Zero lines: stays 0
    WHEN l.is_zero THEN 0
    -- Last non-zero line: absorbs remainder to ensure balance
    WHEN l.is_last_nonzero THEN s.total_amount - inv.other_revenue - inv.total_sep_tax
    -- All other lines: use raw revenue
    ELSE l.raw_revenue
END
FROM _inv_stage s, inv_sums inv
WHERE l.invoice_id = s.invoice_id
  AND l.invoice_id = inv.invoice_id;

\echo 'Step 2 complete: Line staging built'
SELECT
    COUNT(*) AS total_lines,
    COUNT(*) FILTER (WHERE is_zero) AS zero_lines,
    COUNT(*) FILTER (WHERE is_last_nonzero) AS last_lines,
    COUNT(*) FILTER (WHERE has_separate_tax) AS tax_lines
FROM _line_stage;

-- ─────────────────────────────────────────────────────────────────────────────
-- Step 3: Validate before inserting
-- ─────────────────────────────────────────────────────────────────────────────
-- Check: every invoice has at least one non-zero revenue line
DO $$
DECLARE
    bad_count int;
BEGIN
    SELECT COUNT(*) INTO bad_count
    FROM _inv_stage s
    WHERE NOT EXISTS (
        SELECT 1 FROM _line_stage l
        WHERE l.invoice_id = s.invoice_id AND NOT l.is_zero
    );
    IF bad_count > 0 THEN
        RAISE WARNING '% invoices have ALL zero-amount lines — they will get unbalanced journals', bad_count;
    END IF;
END $$;

-- Check: adjusted revenues should make journals balance
-- For standard: sum(adjusted_revenue of non-zero lines) + sum(separate tax) = total_amount
-- Allowing 0.01 tolerance for rounding
DO $$
DECLARE
    bad_count int;
BEGIN
    WITH checks AS (
        SELECT
            s.invoice_id,
            s.total_amount,
            COALESCE(SUM(CASE WHEN NOT l.is_zero THEN l.adjusted_revenue ELSE 0 END), 0) +
            COALESCE(SUM(CASE WHEN l.has_separate_tax THEN l.tax_amount ELSE 0 END), 0) AS credit_total
        FROM _inv_stage s
        LEFT JOIN _line_stage l ON l.invoice_id = s.invoice_id
        GROUP BY s.invoice_id, s.total_amount
    )
    SELECT COUNT(*) INTO bad_count
    FROM checks
    WHERE ABS(total_amount - credit_total) > 0.01;

    IF bad_count > 0 THEN
        RAISE EXCEPTION 'ABORT: % invoices would have unbalanced journals (delta > 0.01)', bad_count;
    END IF;
    RAISE NOTICE 'Validation passed: all journals will balance';
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- Step 4: Insert journal entries
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO gl.journal_entry (
    journal_entry_id, organization_id, journal_number, journal_type,
    entry_date, posting_date, fiscal_period_id,
    description, reference, currency_code, exchange_rate,
    total_debit, total_credit,
    total_debit_functional, total_credit_functional,
    status, is_reversal, is_intercompany,
    source_module, source_document_type, source_document_id,
    correlation_id,
    created_by_user_id, submitted_by_user_id, submitted_at,
    approved_by_user_id, approved_at,
    posted_by_user_id, posted_at,
    posting_batch_id,
    version
)
SELECT
    s.journal_entry_id,
    :org_id::uuid,
    s.journal_number,
    'STANDARD'::journal_type,
    s.invoice_date,     -- entry_date
    s.invoice_date,     -- posting_date (same as invoice date for historical backfill)
    s.fiscal_period_id,
    'AR Invoice ' || s.invoice_number || ' - ' || s.customer_name,
    s.invoice_number,   -- reference
    s.currency_code,
    s.exchange_rate,
    s.total_amount,     -- total_debit
    s.total_amount,     -- total_credit
    s.total_amount,     -- total_debit_functional (NGN, rate=1)
    s.total_amount,     -- total_credit_functional
    'POSTED'::journal_status,
    false,              -- is_reversal
    false,              -- is_intercompany
    'AR',               -- source_module
    'INVOICE',          -- source_document_type
    s.invoice_id,       -- source_document_id
    s.correlation_id,
    :user_id::uuid,     -- created_by
    :user_id::uuid,     -- submitted_by
    NOW(),              -- submitted_at
    :user_id::uuid,     -- approved_by
    NOW(),              -- approved_at
    :user_id::uuid,     -- posted_by
    NOW(),              -- posted_at
    s.posting_batch_id,
    1                   -- version
FROM _inv_stage s;

\echo 'Step 4 complete: Journal entries inserted'
SELECT COUNT(*) AS journals_inserted FROM gl.journal_entry
WHERE source_module = 'AR' AND source_document_type = 'INVOICE' AND status = 'POSTED';

-- ─────────────────────────────────────────────────────────────────────────────
-- Step 5: Insert journal entry lines
-- ─────────────────────────────────────────────────────────────────────────────

-- 5a: AR Control lines (one per invoice)
-- Standard invoice: DEBIT AR control
-- Credit note: CREDIT AR control
INSERT INTO gl.journal_entry_line (
    line_id, journal_entry_id, line_number, account_id,
    description,
    debit_amount, credit_amount,
    debit_amount_functional, credit_amount_functional,
    currency_code, exchange_rate
)
SELECT
    gen_random_uuid(),
    s.journal_entry_id,
    1,  -- line_number (AR control is always line 1)
    s.ar_control_account_id,
    CASE s.invoice_type
        WHEN 'CREDIT_NOTE' THEN 'AR Credit Note: ' || s.customer_name
        ELSE 'AR Invoice: ' || s.customer_name
    END,
    -- Standard: debit AR; Credit note: credit AR
    CASE s.invoice_type
        WHEN 'CREDIT_NOTE' THEN 0
        ELSE s.total_amount
    END,  -- debit
    CASE s.invoice_type
        WHEN 'CREDIT_NOTE' THEN ABS(s.total_amount)
        ELSE 0
    END,  -- credit
    CASE s.invoice_type
        WHEN 'CREDIT_NOTE' THEN 0
        ELSE s.total_amount
    END,  -- debit_functional
    CASE s.invoice_type
        WHEN 'CREDIT_NOTE' THEN ABS(s.total_amount)
        ELSE 0
    END,  -- credit_functional
    s.currency_code,
    s.exchange_rate
FROM _inv_stage s;

-- 5b: Revenue lines (one per non-zero invoice line)
-- Standard + positive: CREDIT revenue
-- Standard + negative (discount): DEBIT revenue (contra-revenue)
-- Credit note + positive: DEBIT revenue
-- Credit note + negative: CREDIT revenue
INSERT INTO gl.journal_entry_line (
    line_id, journal_entry_id, line_number, account_id,
    description,
    debit_amount, credit_amount,
    debit_amount_functional, credit_amount_functional,
    currency_code, exchange_rate,
    cost_center_id, project_id, segment_id
)
SELECT
    l.revenue_line_id,
    s.journal_entry_id,
    -- Line numbers start at 2 (after AR control line)
    1 + ROW_NUMBER() OVER (PARTITION BY l.invoice_id ORDER BY l.line_number),
    l.revenue_account_id,
    -- Description
    CASE
        WHEN s.invoice_type = 'CREDIT_NOTE' THEN 'AR Credit Note: '
        WHEN l.adjusted_revenue < 0 THEN 'AR Discount: '
        ELSE 'AR Invoice: '
    END || COALESCE(l.line_description, s.customer_name),
    -- Debit amount
    CASE
        -- Standard + negative (discount) → debit
        WHEN s.invoice_type != 'CREDIT_NOTE' AND l.adjusted_revenue < 0
            THEN ABS(l.adjusted_revenue)
        -- Credit note + positive → debit
        WHEN s.invoice_type = 'CREDIT_NOTE' AND l.adjusted_revenue > 0
            THEN ABS(l.adjusted_revenue)
        -- Credit note + negative → credit (handled below)
        ELSE 0
    END,
    -- Credit amount
    CASE
        -- Standard + positive → credit
        WHEN s.invoice_type != 'CREDIT_NOTE' AND l.adjusted_revenue > 0
            THEN l.adjusted_revenue
        -- Credit note + negative → credit
        WHEN s.invoice_type = 'CREDIT_NOTE' AND l.adjusted_revenue < 0
            THEN ABS(l.adjusted_revenue)
        ELSE 0
    END,
    -- Functional amounts = transaction amounts (NGN, rate=1)
    CASE
        WHEN s.invoice_type != 'CREDIT_NOTE' AND l.adjusted_revenue < 0
            THEN ABS(l.adjusted_revenue)
        WHEN s.invoice_type = 'CREDIT_NOTE' AND l.adjusted_revenue > 0
            THEN ABS(l.adjusted_revenue)
        ELSE 0
    END,  -- debit_functional
    CASE
        WHEN s.invoice_type != 'CREDIT_NOTE' AND l.adjusted_revenue > 0
            THEN l.adjusted_revenue
        WHEN s.invoice_type = 'CREDIT_NOTE' AND l.adjusted_revenue < 0
            THEN ABS(l.adjusted_revenue)
        ELSE 0
    END,  -- credit_functional
    s.currency_code,
    s.exchange_rate,
    l.cost_center_id,
    l.project_id,
    l.segment_id
FROM _line_stage l
JOIN _inv_stage s ON s.invoice_id = l.invoice_id
WHERE NOT l.is_zero;

-- 5c: Tax lines (where separate tax posting is needed)
-- Standard: CREDIT tax account
-- Credit note: DEBIT tax account
INSERT INTO gl.journal_entry_line (
    line_id, journal_entry_id, line_number, account_id,
    description,
    debit_amount, credit_amount,
    debit_amount_functional, credit_amount_functional,
    currency_code, exchange_rate
)
SELECT
    l.tax_line_id,
    s.journal_entry_id,
    -- Tax line numbers: after all revenue lines
    -- Using a high line number offset to avoid collision
    1000 + l.line_number,
    :tax_acct::uuid,
    CASE s.invoice_type
        WHEN 'CREDIT_NOTE' THEN 'AR Credit Note Tax: ' || COALESCE(l.line_description, '')
        ELSE 'AR Invoice Tax: ' || COALESCE(l.line_description, '')
    END,
    -- Standard: credit tax; Credit note: debit tax
    CASE s.invoice_type
        WHEN 'CREDIT_NOTE' THEN ABS(l.tax_amount)
        ELSE 0
    END,
    CASE s.invoice_type
        WHEN 'CREDIT_NOTE' THEN 0
        ELSE l.tax_amount
    END,
    CASE s.invoice_type
        WHEN 'CREDIT_NOTE' THEN ABS(l.tax_amount)
        ELSE 0
    END,
    CASE s.invoice_type
        WHEN 'CREDIT_NOTE' THEN 0
        ELSE l.tax_amount
    END,
    s.currency_code,
    s.exchange_rate
FROM _line_stage l
JOIN _inv_stage s ON s.invoice_id = l.invoice_id
WHERE l.has_separate_tax;

\echo 'Step 5 complete: Journal lines inserted'

-- ─────────────────────────────────────────────────────────────────────────────
-- Step 6: Insert posting batches
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO gl.posting_batch (
    batch_id, organization_id, fiscal_period_id,
    idempotency_key, source_module, batch_description,
    total_entries, posted_entries, failed_entries,
    status,
    submitted_at, submitted_by_user_id,
    processing_started_at, completed_at,
    correlation_id
)
SELECT
    s.posting_batch_id,
    :org_id::uuid,
    s.fiscal_period_id,
    'ensure-gl-inv-' || s.invoice_id::text,
    'AR',
    'AR Invoice ' || s.invoice_number,
    1, 1, 0,
    'POSTED'::batch_status,
    NOW(), :user_id::uuid,
    NOW(), NOW(),
    s.correlation_id
FROM _inv_stage s;

\echo 'Step 6 complete: Posting batches inserted'

-- ─────────────────────────────────────────────────────────────────────────────
-- Step 7: Insert posted ledger lines (mirror of journal lines)
-- ─────────────────────────────────────────────────────────────────────────────

-- 7a: AR Control ledger lines
INSERT INTO gl.posted_ledger_line (
    ledger_line_id, posting_year, organization_id,
    journal_entry_id, journal_line_id, posting_batch_id, fiscal_period_id,
    account_id, account_code, entry_date, posting_date,
    description, journal_reference,
    debit_amount, credit_amount,
    original_currency_code, original_debit_amount, original_credit_amount,
    exchange_rate,
    source_module, source_document_type, source_document_id,
    posted_by_user_id, correlation_id
)
SELECT
    gen_random_uuid(),
    s.posting_year,
    :org_id::uuid,
    s.journal_entry_id,
    jel.line_id,  -- the AR control journal line
    s.posting_batch_id,
    s.fiscal_period_id,
    s.ar_control_account_id,
    s.ar_control_code,
    s.invoice_date,
    s.invoice_date,
    jel.description,
    s.invoice_number,
    -- Functional amounts = debit/credit from journal line
    jel.debit_amount_functional,
    jel.credit_amount_functional,
    s.currency_code,
    jel.debit_amount,
    jel.credit_amount,
    s.exchange_rate,
    'AR', 'INVOICE', s.invoice_id,
    :user_id::uuid,
    s.correlation_id
FROM _inv_stage s
JOIN gl.journal_entry_line jel
    ON jel.journal_entry_id = s.journal_entry_id
    AND jel.line_number = 1;  -- AR control is line 1

-- 7b: Revenue ledger lines
INSERT INTO gl.posted_ledger_line (
    ledger_line_id, posting_year, organization_id,
    journal_entry_id, journal_line_id, posting_batch_id, fiscal_period_id,
    account_id, account_code, entry_date, posting_date,
    description, journal_reference,
    debit_amount, credit_amount,
    original_currency_code, original_debit_amount, original_credit_amount,
    exchange_rate,
    cost_center_id, project_id, segment_id,
    source_module, source_document_type, source_document_id,
    posted_by_user_id, correlation_id
)
SELECT
    gen_random_uuid(),
    s.posting_year,
    :org_id::uuid,
    s.journal_entry_id,
    jel.line_id,
    s.posting_batch_id,
    s.fiscal_period_id,
    jel.account_id,
    a.account_code,
    s.invoice_date,
    s.invoice_date,
    jel.description,
    s.invoice_number,
    jel.debit_amount_functional,
    jel.credit_amount_functional,
    s.currency_code,
    jel.debit_amount,
    jel.credit_amount,
    s.exchange_rate,
    jel.cost_center_id,
    jel.project_id,
    jel.segment_id,
    'AR', 'INVOICE', s.invoice_id,
    :user_id::uuid,
    s.correlation_id
FROM _line_stage l
JOIN _inv_stage s ON s.invoice_id = l.invoice_id
JOIN gl.journal_entry_line jel ON jel.line_id = l.revenue_line_id
JOIN gl.account a ON a.account_id = jel.account_id
WHERE NOT l.is_zero;

-- 7c: Tax ledger lines
INSERT INTO gl.posted_ledger_line (
    ledger_line_id, posting_year, organization_id,
    journal_entry_id, journal_line_id, posting_batch_id, fiscal_period_id,
    account_id, account_code, entry_date, posting_date,
    description, journal_reference,
    debit_amount, credit_amount,
    original_currency_code, original_debit_amount, original_credit_amount,
    exchange_rate,
    source_module, source_document_type, source_document_id,
    posted_by_user_id, correlation_id
)
SELECT
    gen_random_uuid(),
    s.posting_year,
    :org_id::uuid,
    s.journal_entry_id,
    jel.line_id,
    s.posting_batch_id,
    s.fiscal_period_id,
    :tax_acct::uuid,
    :tax_acct_code,
    s.invoice_date,
    s.invoice_date,
    jel.description,
    s.invoice_number,
    jel.debit_amount_functional,
    jel.credit_amount_functional,
    s.currency_code,
    jel.debit_amount,
    jel.credit_amount,
    s.exchange_rate,
    'AR', 'INVOICE', s.invoice_id,
    :user_id::uuid,
    s.correlation_id
FROM _line_stage l
JOIN _inv_stage s ON s.invoice_id = l.invoice_id
JOIN gl.journal_entry_line jel ON jel.line_id = l.tax_line_id
WHERE l.has_separate_tax;

\echo 'Step 7 complete: Posted ledger lines inserted'

-- ─────────────────────────────────────────────────────────────────────────────
-- Step 8: Update invoice journal_entry_id
-- ─────────────────────────────────────────────────────────────────────────────
UPDATE ar.invoice i
SET journal_entry_id = s.journal_entry_id
FROM _inv_stage s
WHERE i.invoice_id = s.invoice_id;

\echo 'Step 8 complete: Invoice journal_entry_id updated'

-- ─────────────────────────────────────────────────────────────────────────────
-- Step 9: Final validation
-- ─────────────────────────────────────────────────────────────────────────────

-- Check journal balance
SELECT
    'journal_balance' AS check_name,
    COUNT(*) AS journal_count,
    SUM(CASE WHEN ABS(total_debit - total_credit) > 0.01 THEN 1 ELSE 0 END) AS unbalanced
FROM gl.journal_entry
WHERE source_module = 'AR' AND source_document_type = 'INVOICE'
  AND status = 'POSTED'
  AND journal_entry_id IN (SELECT journal_entry_id FROM _inv_stage);

-- Check line-level balance per journal
WITH line_sums AS (
    SELECT
        jel.journal_entry_id,
        SUM(jel.debit_amount) AS total_debit,
        SUM(jel.credit_amount) AS total_credit
    FROM gl.journal_entry_line jel
    WHERE jel.journal_entry_id IN (SELECT journal_entry_id FROM _inv_stage)
    GROUP BY jel.journal_entry_id
)
SELECT
    'line_balance' AS check_name,
    COUNT(*) AS journal_count,
    SUM(CASE WHEN ABS(total_debit - total_credit) > 0.01 THEN 1 ELSE 0 END) AS unbalanced
FROM line_sums;

-- Summary counts
SELECT
    'invoices_posted' AS metric, COUNT(*) AS cnt
    FROM ar.invoice WHERE journal_entry_id IS NOT NULL
UNION ALL
SELECT 'invoices_remaining', COUNT(*)
    FROM ar.invoice WHERE journal_entry_id IS NULL AND total_amount != 0
UNION ALL
SELECT 'total_journals_ar_inv', COUNT(*)
    FROM gl.journal_entry WHERE source_module = 'AR' AND source_document_type = 'INVOICE'
UNION ALL
SELECT 'total_posted_ledger_lines', COUNT(*)
    FROM gl.posted_ledger_line WHERE source_module = 'AR' AND source_document_type = 'INVOICE';

-- Cleanup
DROP TABLE IF EXISTS _inv_stage;
DROP TABLE IF EXISTS _line_stage;

COMMIT;

\echo '=== BULK GL BACKFILL COMPLETE ==='
