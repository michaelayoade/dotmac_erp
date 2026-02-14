-- Bulk GL backfill for AR Payments only
-- Creates journal entries, journal lines, posting batches, and posted ledger lines
-- in a single transaction using bulk SQL inserts.
-- Idempotent: skips already-posted records (journal_entry_id IS NULL check).

BEGIN;

-- Step 1: Create temp table with all data needed + pre-generated UUIDs
CREATE TEMP TABLE _pay_batch AS
SELECT
    p.payment_id,
    p.organization_id,
    p.payment_date,
    p.currency_code,
    COALESCE(p.exchange_rate, 1.0) AS exchange_rate,
    p.amount,
    p.amount * COALESCE(p.exchange_rate, 1.0) AS functional_amount,
    p.payment_number,
    p.reference,
    p.correlation_id,
    -- Accounts
    ba.gl_account_id AS bank_gl_account_id,
    c.ar_control_account_id,
    -- Account codes (denormalized for posted_ledger_line)
    bank_acct.account_code AS bank_account_code,
    ar_acct.account_code AS ar_account_code,
    -- Customer name
    c.legal_name AS customer_name,
    -- Fiscal period
    fp.fiscal_period_id,
    -- Posting year
    EXTRACT(YEAR FROM p.payment_date)::int AS posting_year,
    -- Pre-generate UUIDs
    gen_random_uuid() AS journal_entry_id,
    gen_random_uuid() AS je_line1_id,
    gen_random_uuid() AS je_line2_id,
    gen_random_uuid() AS batch_id,
    gen_random_uuid() AS pll_line1_id,
    gen_random_uuid() AS pll_line2_id,
    -- Sequential number (offset past existing BF-PAY-NNNNNN entries)
    ROW_NUMBER() OVER (ORDER BY p.payment_date, p.payment_id) + 71709 AS row_seq
FROM ar.customer_payment p
JOIN ar.customer c ON c.customer_id = p.customer_id
JOIN banking.bank_accounts ba ON ba.bank_account_id = p.bank_account_id
JOIN gl.account bank_acct ON bank_acct.account_id = ba.gl_account_id
JOIN gl.account ar_acct ON ar_acct.account_id = c.ar_control_account_id
JOIN gl.fiscal_period fp ON p.payment_date BETWEEN fp.start_date AND fp.end_date
WHERE p.journal_entry_id IS NULL
  AND p.status = 'CLEARED'
  AND p.amount <> 0
  AND p.bank_account_id IS NOT NULL
  AND fp.organization_id = p.organization_id;

SELECT count(*) AS payments_to_process FROM _pay_batch;

-- Step 2: Journal entries
INSERT INTO gl.journal_entry (
    journal_entry_id, organization_id, journal_number, journal_type,
    entry_date, posting_date, fiscal_period_id,
    description, reference, currency_code, exchange_rate,
    total_debit, total_credit, total_debit_functional, total_credit_functional,
    status, is_reversal, is_intercompany,
    source_module, source_document_type, source_document_id,
    created_by_user_id, submitted_by_user_id, submitted_at,
    approved_by_user_id, approved_at,
    posted_by_user_id, posted_at,
    posting_batch_id, correlation_id, version
)
SELECT
    b.journal_entry_id,
    b.organization_id,
    'BF-PAY-' || LPAD(b.row_seq::text, 6, '0'),
    'STANDARD',
    b.payment_date,
    b.payment_date,
    b.fiscal_period_id,
    'AR Payment ' || COALESCE(b.payment_number, '') || ' - ' || b.customer_name,
    b.reference,
    b.currency_code,
    b.exchange_rate,
    b.amount,
    b.amount,
    b.functional_amount,
    b.functional_amount,
    'POSTED',
    false, false,
    'AR', 'CUSTOMER_PAYMENT', b.payment_id,
    '00000000-0000-0000-0000-000000000000'::uuid, '00000000-0000-0000-0000-000000000000'::uuid, NOW(),
    '00000000-0000-0000-0000-000000000000'::uuid, NOW(),
    '00000000-0000-0000-0000-000000000000'::uuid, NOW(),
    b.batch_id,
    b.correlation_id,
    1
FROM _pay_batch b;

-- Step 3: Journal entry lines (Debit: Bank, Credit: AR Control)
INSERT INTO gl.journal_entry_line (
    line_id, journal_entry_id, line_number, account_id, description,
    debit_amount, credit_amount, debit_amount_functional, credit_amount_functional,
    currency_code, exchange_rate
)
SELECT
    b.je_line1_id, b.journal_entry_id, 1, b.bank_gl_account_id,
    'AR Payment: ' || COALESCE(b.reference, b.payment_number),
    b.amount, 0, b.functional_amount, 0,
    b.currency_code, b.exchange_rate
FROM _pay_batch b;

INSERT INTO gl.journal_entry_line (
    line_id, journal_entry_id, line_number, account_id, description,
    debit_amount, credit_amount, debit_amount_functional, credit_amount_functional,
    currency_code, exchange_rate
)
SELECT
    b.je_line2_id, b.journal_entry_id, 2, b.ar_control_account_id,
    'Payment from ' || b.customer_name,
    0, b.amount, 0, b.functional_amount,
    b.currency_code, b.exchange_rate
FROM _pay_batch b;

-- Step 4: Posting batches
INSERT INTO gl.posting_batch (
    batch_id, organization_id, fiscal_period_id, idempotency_key,
    source_module, batch_description,
    total_entries, posted_entries, failed_entries,
    status, submitted_by_user_id, submitted_at,
    processing_started_at, completed_at
)
SELECT
    b.batch_id, b.organization_id, b.fiscal_period_id,
    'ensure-gl-pay-' || b.payment_id::text,
    'AR',
    'Backfill: AR Payment ' || COALESCE(b.payment_number, ''),
    2, 2, 0,
    'POSTED',
    '00000000-0000-0000-0000-000000000000'::uuid, NOW(), NOW(), NOW()
FROM _pay_batch b;

-- Step 5: Posted ledger lines
INSERT INTO gl.posted_ledger_line (
    ledger_line_id, posting_year, organization_id,
    journal_entry_id, journal_line_id, posting_batch_id, fiscal_period_id,
    account_id, account_code, entry_date, posting_date,
    description, journal_reference,
    debit_amount, credit_amount,
    original_currency_code, original_debit_amount, original_credit_amount,
    exchange_rate,
    source_module, source_document_type, source_document_id,
    posted_by_user_id
)
SELECT
    b.pll_line1_id, b.posting_year, b.organization_id,
    b.journal_entry_id, b.je_line1_id, b.batch_id, b.fiscal_period_id,
    b.bank_gl_account_id, b.bank_account_code,
    b.payment_date, b.payment_date,
    'AR Payment: ' || COALESCE(b.reference, b.payment_number),
    b.reference,
    b.functional_amount, 0,
    b.currency_code, b.amount, 0,
    b.exchange_rate,
    'AR', 'CUSTOMER_PAYMENT', b.payment_id,
    '00000000-0000-0000-0000-000000000000'::uuid
FROM _pay_batch b;

INSERT INTO gl.posted_ledger_line (
    ledger_line_id, posting_year, organization_id,
    journal_entry_id, journal_line_id, posting_batch_id, fiscal_period_id,
    account_id, account_code, entry_date, posting_date,
    description, journal_reference,
    debit_amount, credit_amount,
    original_currency_code, original_debit_amount, original_credit_amount,
    exchange_rate,
    source_module, source_document_type, source_document_id,
    posted_by_user_id
)
SELECT
    b.pll_line2_id, b.posting_year, b.organization_id,
    b.journal_entry_id, b.je_line2_id, b.batch_id, b.fiscal_period_id,
    b.ar_control_account_id, b.ar_account_code,
    b.payment_date, b.payment_date,
    'Payment from ' || b.customer_name,
    b.reference,
    0, b.functional_amount,
    b.currency_code, 0, b.amount,
    b.exchange_rate,
    'AR', 'CUSTOMER_PAYMENT', b.payment_id,
    '00000000-0000-0000-0000-000000000000'::uuid
FROM _pay_batch b;

-- Step 6: Update payments with journal references
UPDATE ar.customer_payment p
SET journal_entry_id = b.journal_entry_id,
    posting_batch_id = b.batch_id
FROM _pay_batch b
WHERE p.payment_id = b.payment_id;

-- Verification
SELECT 'Backfilled' AS status, count(*) AS count FROM _pay_batch;

SELECT 'Remaining without GL' AS status, count(*) AS count
FROM ar.customer_payment
WHERE journal_entry_id IS NULL
  AND status = 'CLEARED'
  AND amount <> 0
  AND bank_account_id IS NOT NULL;

DROP TABLE _pay_batch;

COMMIT;
