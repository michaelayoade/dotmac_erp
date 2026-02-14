-- Bulk GL Backfill: Expense Claims
-- Posts GL journals for all unposted PAID/APPROVED expense claims.
-- Pattern: Debit 6081 (Transportation & Travelling Expenses), Credit 2110 (WHT / Employee Payable)
--
-- Run inside Docker:
--   docker cp scripts/bulk_gl_backfill_expenses.sql dotmac_erp_db:/tmp/
--   docker exec dotmac_erp_db psql -U postgres -d dotmac_erp -f /tmp/bulk_gl_backfill_expenses.sql

\set org_id '''00000000-0000-0000-0000-000000000001'''
\set user_id '''00000000-0000-0000-0000-000000000000'''
\set expense_account '''3aa268f1-6f2c-4991-9f81-f2170310a47a'''
\set payable_account '''5c4bfaa4-dd26-4448-a9c2-21c8bcc6e256'''

BEGIN;

-- ============================================================
-- Step 1: Build claim staging table
-- ============================================================
\echo '=== Step 1: Build claim staging ==='

DROP TABLE IF EXISTS _exp_stage;
CREATE TEMP TABLE _exp_stage AS
SELECT
    ec.claim_id,
    ec.claim_number,
    ec.claim_date,
    COALESCE(ec.total_approved_amount, ec.total_claimed_amount) AS post_amount,
    ec.currency_code,
    fp.fiscal_period_id,
    p.first_name || ' ' || p.last_name AS employee_name,
    gen_random_uuid() AS journal_entry_id,
    NULL::text AS journal_number
FROM expense.expense_claim ec
JOIN hr.employee e ON e.employee_id = ec.employee_id
JOIN public.people p ON p.id = e.person_id
JOIN gl.fiscal_period fp ON fp.organization_id = ec.organization_id
    AND ec.claim_date BETWEEN fp.start_date AND fp.end_date
WHERE ec.journal_entry_id IS NULL
  AND ec.status IN ('PAID', 'APPROVED')
  AND ec.organization_id = :org_id::uuid
  AND COALESCE(ec.total_approved_amount, ec.total_claimed_amount) != 0;

-- Generate sequential journal numbers
WITH max_seq AS (
    SELECT COALESCE(MAX(CAST(split_part(journal_number, '-', 2) AS integer)), 0) AS seq
    FROM gl.journal_entry
    WHERE organization_id = :org_id::uuid
      AND journal_number LIKE 'JE%-%'
)
UPDATE _exp_stage s
SET journal_number = 'JE' || to_char(CURRENT_DATE, 'YYYYMM') || '-' ||
    (ms.seq + rn)::text
FROM max_seq ms,
     (SELECT claim_id, ROW_NUMBER() OVER (ORDER BY claim_date, claim_id) AS rn
      FROM _exp_stage) r
WHERE s.claim_id = r.claim_id;

\echo '--- Claim staging ---'
SELECT COUNT(*) AS claims_staged FROM _exp_stage;

-- ============================================================
-- Step 2: Build item staging (for multi-line debit entries)
-- ============================================================
\echo '=== Step 2: Build item staging ==='

DROP TABLE IF EXISTS _exp_items;
CREATE TEMP TABLE _exp_items AS
SELECT
    s.claim_id,
    s.journal_entry_id,
    eci.item_id,
    eci.sequence,
    COALESCE(eci.approved_amount, eci.claimed_amount) AS item_amount,
    eci.description AS item_description
FROM _exp_stage s
JOIN expense.expense_claim_item eci ON eci.claim_id = s.claim_id
WHERE COALESCE(eci.approved_amount, eci.claimed_amount) != 0;

-- For claims WITHOUT items, create a synthetic single-line entry
INSERT INTO _exp_items (claim_id, journal_entry_id, item_id, sequence, item_amount, item_description)
SELECT
    s.claim_id,
    s.journal_entry_id,
    gen_random_uuid(),
    1,
    s.post_amount,
    'Expense Claim ' || s.claim_number
FROM _exp_stage s
WHERE NOT EXISTS (
    SELECT 1 FROM _exp_items ei WHERE ei.claim_id = s.claim_id
);

\echo '--- Item staging ---'
SELECT
    COUNT(DISTINCT claim_id) AS claims_with_lines,
    COUNT(*) AS total_item_lines
FROM _exp_items;

-- ============================================================
-- Step 3: Apply "last line absorbs delta" for claims with items
-- ============================================================
\echo '=== Step 3: Delta allocation ==='

ALTER TABLE _exp_items ADD COLUMN is_last boolean DEFAULT false;
ALTER TABLE _exp_items ADD COLUMN adjusted_amount numeric;

WITH last_items AS (
    SELECT DISTINCT ON (claim_id) item_id
    FROM _exp_items
    ORDER BY claim_id, sequence DESC
)
UPDATE _exp_items ei
SET is_last = true
FROM last_items li
WHERE ei.item_id = li.item_id;

WITH item_sums AS (
    SELECT
        ei.claim_id,
        COALESCE(SUM(CASE WHEN NOT ei.is_last THEN ei.item_amount ELSE 0 END), 0) AS other_total
    FROM _exp_items ei
    GROUP BY ei.claim_id
)
UPDATE _exp_items ei
SET adjusted_amount = CASE
    WHEN ei.is_last THEN s.post_amount - isum.other_total
    ELSE ei.item_amount
END
FROM _exp_stage s, item_sums isum
WHERE ei.claim_id = s.claim_id
  AND ei.claim_id = isum.claim_id;

\echo '--- Balance check ---'
WITH sums AS (
    SELECT ei.claim_id,
           s.post_amount,
           SUM(ei.adjusted_amount) AS items_total
    FROM _exp_items ei
    JOIN _exp_stage s ON s.claim_id = ei.claim_id
    GROUP BY ei.claim_id, s.post_amount
)
SELECT
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE ABS(post_amount - items_total) <= 0.01) AS balanced,
    COUNT(*) FILTER (WHERE ABS(post_amount - items_total) > 0.01) AS unbalanced
FROM sums;

-- ============================================================
-- Step 4: Insert journal entries
-- ============================================================
\echo '=== Step 4: Insert journal entries ==='

INSERT INTO gl.journal_entry (
    journal_entry_id, organization_id, journal_number, journal_type,
    entry_date, posting_date, fiscal_period_id,
    description, reference, currency_code, exchange_rate,
    total_debit, total_credit,
    total_debit_functional, total_credit_functional,
    status, is_reversal, is_intercompany,
    source_module, source_document_type, source_document_id,
    created_by_user_id, posted_by_user_id, posted_at, created_at
)
SELECT
    s.journal_entry_id,
    :org_id::uuid,
    s.journal_number,
    'STANDARD'::journal_type,
    s.claim_date,
    s.claim_date,
    s.fiscal_period_id,
    'Expense Claim ' || s.claim_number || ' - ' || s.employee_name,
    s.claim_number,
    COALESCE(s.currency_code, 'NGN'),
    1.0,
    s.post_amount,
    s.post_amount,
    s.post_amount,
    s.post_amount,
    'POSTED'::journal_status, false, false,
    'EXPENSE',
    'EXPENSE_CLAIM',
    s.claim_id,
    :user_id::uuid,
    :user_id::uuid,
    NOW(),
    NOW()
FROM _exp_stage s;

SELECT COUNT(*) AS journals_inserted FROM gl.journal_entry
WHERE source_module = 'EXPENSE'
  AND organization_id = :org_id::uuid
  AND journal_entry_id IN (SELECT journal_entry_id FROM _exp_stage);

-- ============================================================
-- Step 5: Insert journal lines
-- ============================================================
\echo '=== Step 5: Insert journal lines ==='

-- 5a: Debit lines (expense account per item)
INSERT INTO gl.journal_entry_line (
    journal_entry_id, line_number, account_id,
    debit_amount, credit_amount,
    debit_amount_functional, credit_amount_functional,
    description
)
SELECT
    ei.journal_entry_id,
    ei.sequence,
    :expense_account::uuid,
    ei.adjusted_amount,
    0,
    ei.adjusted_amount,
    0,
    COALESCE(ei.item_description, 'Expense: ' || s.claim_number)
FROM _exp_items ei
JOIN _exp_stage s ON s.claim_id = ei.claim_id;

-- 5b: Credit line (employee payable for total)
INSERT INTO gl.journal_entry_line (
    journal_entry_id, line_number, account_id,
    debit_amount, credit_amount,
    debit_amount_functional, credit_amount_functional,
    description
)
SELECT
    s.journal_entry_id,
    COALESCE((SELECT MAX(ei.sequence) FROM _exp_items ei WHERE ei.claim_id = s.claim_id), 0) + 1,
    :payable_account::uuid,
    0,
    s.post_amount,
    0,
    s.post_amount,
    'Employee Payable: ' || s.employee_name
FROM _exp_stage s;

\echo '--- Line counts ---'
SELECT
    COUNT(*) FILTER (WHERE debit_amount > 0) AS debit_lines,
    COUNT(*) FILTER (WHERE credit_amount > 0) AS credit_lines,
    COUNT(*) AS total_lines
FROM gl.journal_entry_line jel
WHERE jel.journal_entry_id IN (SELECT journal_entry_id FROM _exp_stage);

-- ============================================================
-- Step 6: Insert posting batches
-- ============================================================
\echo '=== Step 6: Insert posting batches ==='

-- Delete stale batches from prior failed runs (for claims still needing posting)
DELETE FROM gl.posting_batch pb
WHERE pb.idempotency_key IN (
    SELECT 'ensure-gl-exp-' || s.claim_id::text FROM _exp_stage s
);

INSERT INTO gl.posting_batch (
    batch_id, organization_id, fiscal_period_id,
    idempotency_key, source_module,
    batch_description,
    total_entries, posted_entries, failed_entries,
    status,
    submitted_by_user_id, submitted_at, completed_at
)
SELECT
    gen_random_uuid(),
    :org_id::uuid,
    s.fiscal_period_id,
    'ensure-gl-exp-' || s.claim_id::text,
    'EXPENSE',
    'Journal ' || s.journal_number,
    1, 1, 0,
    'POSTED'::batch_status,
    :user_id::uuid,
    NOW(),
    NOW()
FROM _exp_stage s;

SELECT COUNT(*) AS batches_inserted FROM gl.posting_batch
WHERE source_module = 'EXPENSE'
  AND organization_id = :org_id::uuid
  AND idempotency_key LIKE 'ensure-gl-exp-%';

-- ============================================================
-- Step 7: Insert posted ledger lines
-- ============================================================
\echo '=== Step 7: Insert posted ledger lines ==='

INSERT INTO gl.posted_ledger_line (
    ledger_line_id, posting_year, organization_id,
    journal_entry_id, journal_line_id, posting_batch_id,
    fiscal_period_id, account_id, account_code,
    entry_date, posting_date,
    description, journal_reference,
    debit_amount, credit_amount,
    original_currency_code, original_debit_amount, original_credit_amount,
    exchange_rate,
    source_module, source_document_type, source_document_id,
    posted_at, posted_by_user_id
)
SELECT
    gen_random_uuid(),
    EXTRACT(YEAR FROM s.claim_date)::integer,
    :org_id::uuid,
    jel.journal_entry_id,
    jel.line_id,
    pb.batch_id,
    s.fiscal_period_id,
    jel.account_id,
    a.account_code,
    s.claim_date,
    s.claim_date,
    jel.description,
    s.claim_number,
    jel.debit_amount,
    jel.credit_amount,
    COALESCE(s.currency_code, 'NGN'),
    jel.debit_amount,
    jel.credit_amount,
    1.0,
    'EXPENSE',
    'EXPENSE_CLAIM',
    s.claim_id,
    NOW(),
    :user_id::uuid
FROM _exp_stage s
JOIN gl.journal_entry_line jel ON jel.journal_entry_id = s.journal_entry_id
JOIN gl.account a ON a.account_id = jel.account_id
JOIN gl.posting_batch pb ON pb.idempotency_key = 'ensure-gl-exp-' || s.claim_id::text
     AND pb.organization_id = :org_id::uuid;

SELECT COUNT(*) AS ledger_lines_inserted FROM gl.posted_ledger_line
WHERE source_module = 'EXPENSE'
  AND organization_id = :org_id::uuid
  AND journal_entry_id IN (SELECT journal_entry_id FROM _exp_stage);

-- ============================================================
-- Step 8: Update expense_claim.journal_entry_id
-- ============================================================
\echo '=== Step 8: Update expense_claim.journal_entry_id ==='

UPDATE expense.expense_claim ec
SET journal_entry_id = s.journal_entry_id
FROM _exp_stage s
WHERE ec.claim_id = s.claim_id;

SELECT COUNT(*) AS total_claims_with_journal FROM expense.expense_claim
WHERE journal_entry_id IS NOT NULL
  AND organization_id = :org_id::uuid;

-- ============================================================
-- Step 9: Validation
-- ============================================================
\echo '=== Step 9: Final validation ==='

-- Header balance check
WITH bal AS (
    SELECT je.journal_entry_id,
           je.total_debit - je.total_credit AS header_delta
    FROM gl.journal_entry je
    WHERE je.journal_entry_id IN (SELECT journal_entry_id FROM _exp_stage)
)
SELECT
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE ABS(header_delta) <= 0.01) AS balanced,
    COUNT(*) FILTER (WHERE ABS(header_delta) > 0.01) AS unbalanced
FROM bal;

-- Line balance check
WITH line_bal AS (
    SELECT jel.journal_entry_id,
           SUM(jel.debit_amount) - SUM(jel.credit_amount) AS line_delta
    FROM gl.journal_entry_line jel
    WHERE jel.journal_entry_id IN (SELECT journal_entry_id FROM _exp_stage)
    GROUP BY jel.journal_entry_id
)
SELECT
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE ABS(line_delta) <= 0.01) AS balanced,
    COUNT(*) FILTER (WHERE ABS(line_delta) > 0.01) AS unbalanced
FROM line_bal;

-- Remaining unposted
SELECT COUNT(*) AS remaining_unposted_paid_approved
FROM expense.expense_claim
WHERE journal_entry_id IS NULL
  AND status IN ('PAID', 'APPROVED')
  AND organization_id = :org_id::uuid;

-- Cleanup
DROP TABLE IF EXISTS _exp_stage;
DROP TABLE IF EXISTS _exp_items;

COMMIT;

\echo '=== DONE ==='
