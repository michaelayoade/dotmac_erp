\set org_id '''00000000-0000-0000-0000-000000000001'''
\set user_id '''00000000-0000-0000-0000-000000000000'''
\set tax_code '''4b180259-b0b0-41fb-955b-0e089df66b42'''
\set tax_acct '''d6fcaecf-e1b7-4dce-9743-368eb5b1775c'''
\set tax_acct_code '''2000'''

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- Step 1: Find all unbalanced credit note journals
-- ─────────────────────────────────────────────────────────────────────────────
DROP TABLE IF EXISTS _bad_journals;
CREATE TEMP TABLE _bad_journals AS
WITH line_sums AS (
    SELECT jel.journal_entry_id
    FROM gl.journal_entry_line jel
    JOIN gl.journal_entry je ON je.journal_entry_id = jel.journal_entry_id
    WHERE je.source_module = 'AR' AND je.source_document_type = 'INVOICE'
      AND je.status = 'POSTED'
    GROUP BY jel.journal_entry_id
    HAVING ABS(SUM(jel.debit_amount) - SUM(jel.credit_amount)) > 0.01
)
SELECT ls.journal_entry_id, i.invoice_id
FROM line_sums ls
JOIN ar.invoice i ON i.journal_entry_id = ls.journal_entry_id;

\echo 'Unbalanced journals to fix:'
SELECT COUNT(*) FROM _bad_journals;

-- ─────────────────────────────────────────────────────────────────────────────
-- Step 2: Delete broken data
-- ─────────────────────────────────────────────────────────────────────────────

-- Delete posted ledger lines
DELETE FROM gl.posted_ledger_line
WHERE journal_entry_id IN (SELECT journal_entry_id FROM _bad_journals);

-- Delete posting batches
DELETE FROM gl.posting_batch
WHERE batch_id IN (
    SELECT posting_batch_id FROM gl.journal_entry
    WHERE journal_entry_id IN (SELECT journal_entry_id FROM _bad_journals)
);

-- Delete journal lines
DELETE FROM gl.journal_entry_line
WHERE journal_entry_id IN (SELECT journal_entry_id FROM _bad_journals);

-- Delete journal entries
DELETE FROM gl.journal_entry
WHERE journal_entry_id IN (SELECT journal_entry_id FROM _bad_journals);

-- Reset invoice journal_entry_id
UPDATE ar.invoice
SET journal_entry_id = NULL
WHERE invoice_id IN (SELECT invoice_id FROM _bad_journals);

\echo 'Broken data deleted, invoices reset'

-- ─────────────────────────────────────────────────────────────────────────────
-- Step 3: Re-create with proper ABS() handling for credit notes
-- ─────────────────────────────────────────────────────────────────────────────

-- Invoice staging (credit notes only)
DROP TABLE IF EXISTS _inv_stage;
CREATE TEMP TABLE _inv_stage AS
SELECT
    i.invoice_id,
    i.invoice_number,
    i.invoice_date,
    i.total_amount,
    -- For credit notes, use ABS for all amounts
    ABS(i.total_amount) AS abs_total,
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

-- Assign journal numbers
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

ALTER TABLE _inv_stage ADD COLUMN journal_number varchar(30);
UPDATE _inv_stage SET journal_number = 'JE' || TO_CHAR(invoice_date, 'YYYYMM') || '-' || seq_num::text;

\echo 'Credit note staging:'
SELECT COUNT(*) AS invoices, invoice_type FROM _inv_stage GROUP BY invoice_type;

-- Line staging
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
    CASE WHEN il.tax_code_id = :tax_code::uuid AND COALESCE(il.tax_amount, 0) != 0
         THEN true ELSE false END AS has_separate_tax,
    -- Revenue: line_amount + (tax if no separate account)
    -- Use ABS because credit notes have negative amounts
    ABS(il.line_amount + CASE
        WHEN il.tax_code_id = :tax_code::uuid AND COALESCE(il.tax_amount, 0) != 0
        THEN 0 ELSE COALESCE(il.tax_amount, 0) END) AS abs_revenue,
    gen_random_uuid() AS revenue_line_id,
    gen_random_uuid() AS tax_line_id
FROM ar.invoice_line il
JOIN _inv_stage s ON s.invoice_id = il.invoice_id;

-- Mark zero lines and find last nonzero
ALTER TABLE _line_stage ADD COLUMN is_zero boolean DEFAULT false;
UPDATE _line_stage SET is_zero = true WHERE abs_revenue = 0;

ALTER TABLE _line_stage ADD COLUMN is_last_nonzero boolean DEFAULT false;
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

-- Adjusted revenue (last line absorbs remainder)
-- For credit notes: abs_total = ABS(total_amount), so all amounts are positive
ALTER TABLE _line_stage ADD COLUMN adjusted_revenue numeric;
WITH inv_sums AS (
    SELECT
        l.invoice_id,
        COALESCE(SUM(CASE WHEN NOT l.is_zero AND NOT l.is_last_nonzero
                          THEN l.abs_revenue ELSE 0 END), 0) AS other_revenue,
        COALESCE(SUM(CASE WHEN l.has_separate_tax THEN ABS(l.tax_amount) ELSE 0 END), 0) AS total_sep_tax
    FROM _line_stage l
    GROUP BY l.invoice_id
)
UPDATE _line_stage l
SET adjusted_revenue = CASE
    WHEN l.is_zero THEN 0
    WHEN l.is_last_nonzero THEN s.abs_total - inv.other_revenue - inv.total_sep_tax
    ELSE l.abs_revenue
END
FROM _inv_stage s, inv_sums inv
WHERE l.invoice_id = s.invoice_id
  AND l.invoice_id = inv.invoice_id;

-- Validate balance
DO $$
DECLARE bad_count int;
BEGIN
    WITH checks AS (
        SELECT s.invoice_id, s.abs_total,
            COALESCE(SUM(CASE WHEN NOT l.is_zero THEN l.adjusted_revenue ELSE 0 END), 0) +
            COALESCE(SUM(CASE WHEN l.has_separate_tax THEN ABS(l.tax_amount) ELSE 0 END), 0) AS credit_total
        FROM _inv_stage s
        LEFT JOIN _line_stage l ON l.invoice_id = s.invoice_id
        GROUP BY s.invoice_id, s.abs_total
    )
    SELECT COUNT(*) INTO bad_count FROM checks WHERE ABS(abs_total - credit_total) > 0.01;
    IF bad_count > 0 THEN
        RAISE EXCEPTION 'ABORT: % invoices would be unbalanced', bad_count;
    END IF;
    RAISE NOTICE 'Validation passed: all % credit note journals will balance',
        (SELECT COUNT(*) FROM _inv_stage);
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
    posting_batch_id, version
)
SELECT
    s.journal_entry_id, :org_id::uuid, s.journal_number, 'STANDARD'::journal_type,
    s.invoice_date, s.invoice_date, s.fiscal_period_id,
    'AR Credit Note ' || s.invoice_number || ' - ' || s.customer_name,
    s.invoice_number, s.currency_code, s.exchange_rate,
    -- Header totals use ABS for credit notes
    s.abs_total, s.abs_total,
    s.abs_total, s.abs_total,
    'POSTED'::journal_status, false, false,
    'AR', 'INVOICE', s.invoice_id,
    s.correlation_id,
    :user_id::uuid, :user_id::uuid, NOW(),
    :user_id::uuid, NOW(),
    :user_id::uuid, NOW(),
    s.posting_batch_id, 1
FROM _inv_stage s;

-- ─────────────────────────────────────────────────────────────────────────────
-- Step 5: Insert journal lines
-- ─────────────────────────────────────────────────────────────────────────────

-- 5a: AR Control line — CREDIT for credit notes
INSERT INTO gl.journal_entry_line (
    line_id, journal_entry_id, line_number, account_id,
    description, debit_amount, credit_amount,
    debit_amount_functional, credit_amount_functional,
    currency_code, exchange_rate
)
SELECT
    gen_random_uuid(), s.journal_entry_id, 1, s.ar_control_account_id,
    'AR Credit Note: ' || s.customer_name,
    0, s.abs_total,   -- Credit AR for credit notes
    0, s.abs_total,
    s.currency_code, s.exchange_rate
FROM _inv_stage s;

-- 5b: Revenue lines — DEBIT for credit notes
INSERT INTO gl.journal_entry_line (
    line_id, journal_entry_id, line_number, account_id,
    description, debit_amount, credit_amount,
    debit_amount_functional, credit_amount_functional,
    currency_code, exchange_rate,
    cost_center_id, project_id, segment_id
)
SELECT
    l.revenue_line_id, s.journal_entry_id,
    1 + ROW_NUMBER() OVER (PARTITION BY l.invoice_id ORDER BY l.line_number),
    l.revenue_account_id,
    'AR Credit Note: ' || COALESCE(l.line_description, s.customer_name),
    l.adjusted_revenue, 0,  -- Debit revenue for credit notes
    l.adjusted_revenue, 0,
    s.currency_code, s.exchange_rate,
    l.cost_center_id, l.project_id, l.segment_id
FROM _line_stage l
JOIN _inv_stage s ON s.invoice_id = l.invoice_id
WHERE NOT l.is_zero;

-- 5c: Tax lines — DEBIT for credit notes
INSERT INTO gl.journal_entry_line (
    line_id, journal_entry_id, line_number, account_id,
    description, debit_amount, credit_amount,
    debit_amount_functional, credit_amount_functional,
    currency_code, exchange_rate
)
SELECT
    l.tax_line_id, s.journal_entry_id,
    1000 + l.line_number,
    :tax_acct::uuid,
    'AR Credit Note Tax: ' || COALESCE(l.line_description, ''),
    ABS(l.tax_amount), 0,  -- Debit tax for credit notes
    ABS(l.tax_amount), 0,
    s.currency_code, s.exchange_rate
FROM _line_stage l
JOIN _inv_stage s ON s.invoice_id = l.invoice_id
WHERE l.has_separate_tax;

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
    s.posting_batch_id, :org_id::uuid, s.fiscal_period_id,
    'ensure-gl-inv-' || s.invoice_id::text, 'AR',
    'AR Credit Note ' || s.invoice_number,
    1, 1, 0, 'POSTED'::batch_status,
    NOW(), :user_id::uuid, NOW(), NOW(),
    s.correlation_id
FROM _inv_stage s;

-- ─────────────────────────────────────────────────────────────────────────────
-- Step 7: Insert posted ledger lines
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
    gen_random_uuid(), s.posting_year, :org_id::uuid,
    s.journal_entry_id, jel.line_id, s.posting_batch_id, s.fiscal_period_id,
    s.ar_control_account_id, s.ar_control_code,
    s.invoice_date, s.invoice_date,
    jel.description, s.invoice_number,
    jel.debit_amount_functional, jel.credit_amount_functional,
    s.currency_code, jel.debit_amount, jel.credit_amount,
    s.exchange_rate,
    'AR', 'INVOICE', s.invoice_id,
    :user_id::uuid, s.correlation_id
FROM _inv_stage s
JOIN gl.journal_entry_line jel ON jel.journal_entry_id = s.journal_entry_id AND jel.line_number = 1;

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
    gen_random_uuid(), s.posting_year, :org_id::uuid,
    s.journal_entry_id, jel.line_id, s.posting_batch_id, s.fiscal_period_id,
    jel.account_id, a.account_code,
    s.invoice_date, s.invoice_date,
    jel.description, s.invoice_number,
    jel.debit_amount_functional, jel.credit_amount_functional,
    s.currency_code, jel.debit_amount, jel.credit_amount,
    s.exchange_rate,
    jel.cost_center_id, jel.project_id, jel.segment_id,
    'AR', 'INVOICE', s.invoice_id,
    :user_id::uuid, s.correlation_id
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
    gen_random_uuid(), s.posting_year, :org_id::uuid,
    s.journal_entry_id, jel.line_id, s.posting_batch_id, s.fiscal_period_id,
    :tax_acct::uuid, :tax_acct_code,
    s.invoice_date, s.invoice_date,
    jel.description, s.invoice_number,
    jel.debit_amount_functional, jel.credit_amount_functional,
    s.currency_code, jel.debit_amount, jel.credit_amount,
    s.exchange_rate,
    'AR', 'INVOICE', s.invoice_id,
    :user_id::uuid, s.correlation_id
FROM _line_stage l
JOIN _inv_stage s ON s.invoice_id = l.invoice_id
JOIN gl.journal_entry_line jel ON jel.line_id = l.tax_line_id
WHERE l.has_separate_tax;

-- ─────────────────────────────────────────────────────────────────────────────
-- Step 8: Update invoice journal_entry_id
-- ─────────────────────────────────────────────────────────────────────────────
UPDATE ar.invoice i
SET journal_entry_id = s.journal_entry_id
FROM _inv_stage s
WHERE i.invoice_id = s.invoice_id;

-- ─────────────────────────────────────────────────────────────────────────────
-- Step 9: Validate
-- ─────────────────────────────────────────────────────────────────────────────
\echo '--- Final balance check ---'
WITH line_sums AS (
    SELECT jel.journal_entry_id,
        SUM(jel.debit_amount) AS total_debit,
        SUM(jel.credit_amount) AS total_credit
    FROM gl.journal_entry_line jel
    JOIN gl.journal_entry je ON je.journal_entry_id = jel.journal_entry_id
    WHERE je.source_module = 'AR' AND je.source_document_type = 'INVOICE'
      AND je.status = 'POSTED'
    GROUP BY jel.journal_entry_id
    HAVING ABS(SUM(jel.debit_amount) - SUM(jel.credit_amount)) > 0.01
)
SELECT COUNT(*) AS still_unbalanced FROM line_sums;

SELECT
    'invoices_posted' AS metric, COUNT(*) FROM ar.invoice WHERE journal_entry_id IS NOT NULL
UNION ALL
SELECT 'invoices_remaining', COUNT(*) FROM ar.invoice WHERE journal_entry_id IS NULL AND total_amount != 0;

DROP TABLE IF EXISTS _bad_journals;
DROP TABLE IF EXISTS _inv_stage;
DROP TABLE IF EXISTS _line_stage;

COMMIT;
\echo '=== CREDIT NOTE FIX COMPLETE ==='
