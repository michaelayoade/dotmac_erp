-- =============================================================================
-- Bulk GL Posting Backfill
-- =============================================================================
-- Processes ~94K AR invoices, ~60K AR payments in bulk SQL instead of
-- Python ORM (which runs at ~1 record/sec = 45+ hours).
--
-- Each entity needs 4 table inserts:
--   1. gl.journal_entry (1 per source document)
--   2. gl.journal_entry_line (2 per source document)
--   3. gl.posting_batch (1 per source document)
--   4. gl.posted_ledger_line (2 per source document)
-- Plus UPDATE on the source table to set journal_entry_id + posting_batch_id.
--
-- Safety: All wrapped in transactions. Can be re-run idempotently (skips
-- already-posted records via journal_entry_id IS NULL check).
-- =============================================================================

-- Configuration
\set org_id '00000000-0000-0000-0000-000000000001'
\set system_user_id '00000000-0000-0000-0000-000000000001'
\set backfill_user_id '00000000-0000-0000-0000-000000000000'

-- =============================================================================
-- PHASE 1: AR INVOICES
-- =============================================================================
-- Structure:
--   Debit:  AR Control (customer.ar_control_account_id) = invoice total
--   Credit: Revenue (invoice_line.revenue_account_id)   = invoice total
-- All invoices have 1 line, same 2 accounts (1400 debit, 4000 credit).
-- Credit notes (total_amount < 0): swap debit/credit.
-- =============================================================================

\echo '=== PHASE 1: AR INVOICES ==='
\echo 'Starting AR invoice backfill...'

BEGIN;

-- Step 1: Create temp table with all data needed + pre-generated UUIDs
CREATE TEMP TABLE _inv_batch AS
SELECT
    i.invoice_id,
    i.organization_id,
    i.invoice_date,
    i.currency_code,
    i.exchange_rate,
    i.total_amount,
    i.functional_currency_amount,
    i.invoice_number,
    i.correlation_id,
    -- Accounts
    c.ar_control_account_id,
    il.revenue_account_id,
    -- Account codes (denormalized for posted_ledger_line)
    ar_acct.account_code AS ar_account_code,
    rev_acct.account_code AS revenue_account_code,
    -- Customer name for description
    c.legal_name AS customer_name,
    -- Fiscal period
    fp.fiscal_period_id,
    -- Posting year
    EXTRACT(YEAR FROM i.invoice_date)::int AS posting_year,
    -- Pre-generate UUIDs
    gen_random_uuid() AS journal_entry_id,
    gen_random_uuid() AS je_line1_id,  -- debit line
    gen_random_uuid() AS je_line2_id,  -- credit line
    gen_random_uuid() AS batch_id,
    gen_random_uuid() AS pll_line1_id, -- debit ledger line
    gen_random_uuid() AS pll_line2_id, -- credit ledger line
    -- Journal number (sequential)
    ROW_NUMBER() OVER (ORDER BY i.invoice_date, i.invoice_id) AS row_seq,
    -- Credit note flag
    CASE WHEN i.total_amount < 0 THEN true ELSE false END AS is_credit_note,
    -- Absolute amounts for journal lines
    ABS(i.total_amount) AS abs_amount,
    ABS(i.functional_currency_amount) AS abs_functional
FROM ar.invoice i
JOIN ar.customer c ON c.customer_id = i.customer_id
JOIN ar.invoice_line il ON il.invoice_id = i.invoice_id
JOIN gl.fiscal_period fp ON i.invoice_date BETWEEN fp.start_date AND fp.end_date
JOIN gl.account ar_acct ON ar_acct.account_id = c.ar_control_account_id
JOIN gl.account rev_acct ON rev_acct.account_id = il.revenue_account_id
WHERE i.journal_entry_id IS NULL
  AND i.status IN ('APPROVED', 'PAID', 'PARTIALLY_PAID', 'POSTED')
  AND i.total_amount != 0
  AND i.organization_id = :'org_id'::uuid;

\echo 'Temp table created with invoice data'
SELECT count(*) AS invoices_to_process FROM _inv_batch;

-- Step 2: Get the current max journal number for numbering
-- Journal format: JEyyyymm-NNNNN (monthly reset)
-- We'll use a simple sequential format for backfill: BF-INV-NNNNN
-- Actually, let's use proper format grouped by month

-- Step 3: Insert journal entries
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
    'BF-INV-' || LPAD(b.row_seq::text, 6, '0'),  -- Backfill journal number
    'STANDARD',
    b.invoice_date,       -- entry_date
    b.invoice_date,       -- posting_date (same as entry for backfill)
    b.fiscal_period_id,
    'AR Invoice ' || b.invoice_number || ' - ' || b.customer_name,
    b.invoice_number,     -- reference
    b.currency_code,
    COALESCE(b.exchange_rate, 1.0),
    b.abs_amount,         -- total_debit
    b.abs_amount,         -- total_credit
    b.abs_functional,     -- total_debit_functional
    b.abs_functional,     -- total_credit_functional
    'POSTED',
    false,                -- is_reversal
    false,                -- is_intercompany
    'AR',
    'INVOICE',
    b.invoice_id,
    :'backfill_user_id'::uuid,  -- created_by
    :'backfill_user_id'::uuid,  -- submitted_by
    NOW(),                       -- submitted_at
    :'backfill_user_id'::uuid,  -- approved_by
    NOW(),                       -- approved_at
    :'backfill_user_id'::uuid,  -- posted_by
    NOW(),                       -- posted_at
    b.batch_id,
    b.correlation_id,
    1
FROM _inv_batch b;

\echo 'Journal entries inserted'
SELECT count(*) AS journal_entries FROM gl.journal_entry WHERE source_module = 'AR' AND source_document_type = 'INVOICE';

-- Step 4: Insert journal entry lines (2 per invoice)
-- Line 1: Debit AR Control (or Credit for credit notes)
INSERT INTO gl.journal_entry_line (
    line_id, journal_entry_id, line_number, account_id, description,
    debit_amount, credit_amount, debit_amount_functional, credit_amount_functional,
    currency_code, exchange_rate
)
SELECT
    b.je_line1_id,
    b.journal_entry_id,
    1,
    b.ar_control_account_id,
    'AR Invoice: ' || b.customer_name,
    CASE WHEN NOT b.is_credit_note THEN b.abs_amount ELSE 0 END,       -- debit
    CASE WHEN b.is_credit_note THEN b.abs_amount ELSE 0 END,           -- credit
    CASE WHEN NOT b.is_credit_note THEN b.abs_functional ELSE 0 END,   -- debit_functional
    CASE WHEN b.is_credit_note THEN b.abs_functional ELSE 0 END,       -- credit_functional
    b.currency_code,
    COALESCE(b.exchange_rate, 1.0)
FROM _inv_batch b;

-- Line 2: Credit Revenue (or Debit for credit notes)
INSERT INTO gl.journal_entry_line (
    line_id, journal_entry_id, line_number, account_id, description,
    debit_amount, credit_amount, debit_amount_functional, credit_amount_functional,
    currency_code, exchange_rate
)
SELECT
    b.je_line2_id,
    b.journal_entry_id,
    2,
    b.revenue_account_id,
    'AR Invoice: ' || b.invoice_number,
    CASE WHEN b.is_credit_note THEN b.abs_amount ELSE 0 END,           -- debit
    CASE WHEN NOT b.is_credit_note THEN b.abs_amount ELSE 0 END,       -- credit
    CASE WHEN b.is_credit_note THEN b.abs_functional ELSE 0 END,       -- debit_functional
    CASE WHEN NOT b.is_credit_note THEN b.abs_functional ELSE 0 END,   -- credit_functional
    b.currency_code,
    COALESCE(b.exchange_rate, 1.0)
FROM _inv_batch b;

\echo 'Journal entry lines inserted'

-- Step 5: Insert posting batches (1 per invoice)
INSERT INTO gl.posting_batch (
    batch_id, organization_id, fiscal_period_id, idempotency_key,
    source_module, batch_description,
    total_entries, posted_entries, failed_entries,
    status, submitted_by_user_id, submitted_at,
    processing_started_at, completed_at
)
SELECT
    b.batch_id,
    b.organization_id,
    b.fiscal_period_id,
    'ensure-gl-inv-' || b.invoice_id::text,  -- matches Python idempotency key pattern
    'AR',
    'Backfill: AR Invoice ' || b.invoice_number,
    2,   -- total_entries (2 ledger lines)
    2,   -- posted_entries
    0,   -- failed_entries
    'POSTED',
    :'backfill_user_id'::uuid,
    NOW(),
    NOW(),
    NOW()
FROM _inv_batch b;

\echo 'Posting batches inserted'

-- Step 6: Insert posted ledger lines (2 per invoice)
-- Line 1: AR Control (debit for normal, credit for credit notes)
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
    b.pll_line1_id,
    b.posting_year,
    b.organization_id,
    b.journal_entry_id,
    b.je_line1_id,      -- journal_line_id matches the JE line
    b.batch_id,
    b.fiscal_period_id,
    b.ar_control_account_id,
    b.ar_account_code,
    b.invoice_date,
    b.invoice_date,
    'AR Invoice: ' || b.customer_name,
    b.invoice_number,
    -- posted_ledger_line uses FUNCTIONAL amounts as debit/credit
    CASE WHEN NOT b.is_credit_note THEN b.abs_functional ELSE 0 END,
    CASE WHEN b.is_credit_note THEN b.abs_functional ELSE 0 END,
    -- original amounts
    b.currency_code,
    CASE WHEN NOT b.is_credit_note THEN b.abs_amount ELSE 0 END,
    CASE WHEN b.is_credit_note THEN b.abs_amount ELSE 0 END,
    COALESCE(b.exchange_rate, 1.0),
    'AR', 'INVOICE', b.invoice_id,
    :'backfill_user_id'::uuid
FROM _inv_batch b;

-- Line 2: Revenue (credit for normal, debit for credit notes)
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
    b.pll_line2_id,
    b.posting_year,
    b.organization_id,
    b.journal_entry_id,
    b.je_line2_id,
    b.batch_id,
    b.fiscal_period_id,
    b.revenue_account_id,
    b.revenue_account_code,
    b.invoice_date,
    b.invoice_date,
    'AR Invoice: ' || b.invoice_number,
    b.invoice_number,
    CASE WHEN b.is_credit_note THEN b.abs_functional ELSE 0 END,
    CASE WHEN NOT b.is_credit_note THEN b.abs_functional ELSE 0 END,
    b.currency_code,
    CASE WHEN b.is_credit_note THEN b.abs_amount ELSE 0 END,
    CASE WHEN NOT b.is_credit_note THEN b.abs_amount ELSE 0 END,
    COALESCE(b.exchange_rate, 1.0),
    'AR', 'INVOICE', b.invoice_id,
    :'backfill_user_id'::uuid
FROM _inv_batch b;

\echo 'Posted ledger lines inserted'

-- Step 7: Update invoices with journal references
UPDATE ar.invoice i
SET journal_entry_id = b.journal_entry_id,
    posting_batch_id = b.batch_id,
    posting_status = 'POSTED'
FROM _inv_batch b
WHERE i.invoice_id = b.invoice_id;

\echo 'Invoices updated with journal references'

-- Step 8: Verify
SELECT 'AR Invoices remaining' AS check_name,
       count(*) AS count
FROM ar.invoice
WHERE journal_entry_id IS NULL
  AND status IN ('APPROVED', 'PAID', 'PARTIALLY_PAID', 'POSTED')
  AND total_amount != 0
  AND organization_id = :'org_id'::uuid;

DROP TABLE _inv_batch;

COMMIT;
\echo '=== PHASE 1 COMPLETE ==='


-- =============================================================================
-- PHASE 2: AR PAYMENTS
-- =============================================================================
-- Structure:
--   Debit:  Bank GL Account (via banking.bank_accounts.gl_account_id)
--   Credit: AR Control (customer.ar_control_account_id)
-- Only payments with bank_account_id are postable.
-- =============================================================================

\echo ''
\echo '=== PHASE 2: AR PAYMENTS ==='
\echo 'Starting AR payment backfill...'

BEGIN;

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
    -- Account codes
    bank_acct.account_code AS bank_account_code,
    ar_acct.account_code AS ar_account_code,
    -- Customer name
    c.legal_name AS customer_name,
    -- Fiscal period
    fp.fiscal_period_id,
    -- Posting year
    EXTRACT(YEAR FROM p.payment_date)::int AS posting_year,
    -- UUIDs
    gen_random_uuid() AS journal_entry_id,
    gen_random_uuid() AS je_line1_id,
    gen_random_uuid() AS je_line2_id,
    gen_random_uuid() AS batch_id,
    gen_random_uuid() AS pll_line1_id,
    gen_random_uuid() AS pll_line2_id,
    -- Sequential number
    ROW_NUMBER() OVER (ORDER BY p.payment_date, p.payment_id) AS row_seq
FROM ar.customer_payment p
JOIN ar.customer c ON c.customer_id = p.customer_id
JOIN banking.bank_accounts ba ON ba.bank_account_id = p.bank_account_id
JOIN gl.account bank_acct ON bank_acct.account_id = ba.gl_account_id
JOIN gl.account ar_acct ON ar_acct.account_id = c.ar_control_account_id
JOIN gl.fiscal_period fp ON p.payment_date BETWEEN fp.start_date AND fp.end_date
WHERE p.journal_entry_id IS NULL
  AND p.status = 'CLEARED'
  AND p.amount != 0
  AND p.bank_account_id IS NOT NULL
  AND p.organization_id = :'org_id'::uuid;

\echo 'Temp table created with payment data'
SELECT count(*) AS payments_to_process FROM _pay_batch;

-- Journal entries
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
    :'backfill_user_id'::uuid, :'backfill_user_id'::uuid, NOW(),
    :'backfill_user_id'::uuid, NOW(),
    :'backfill_user_id'::uuid, NOW(),
    b.batch_id,
    b.correlation_id,
    1
FROM _pay_batch b;

\echo 'Payment journal entries inserted'

-- Journal entry lines
-- Line 1: Debit Bank GL Account
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

-- Line 2: Credit AR Control
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

\echo 'Payment journal lines inserted'

-- Posting batches
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
    :'backfill_user_id'::uuid, NOW(), NOW(), NOW()
FROM _pay_batch b;

\echo 'Payment posting batches inserted'

-- Posted ledger lines
-- Line 1: Debit Bank
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
    :'backfill_user_id'::uuid
FROM _pay_batch b;

-- Line 2: Credit AR Control
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
    :'backfill_user_id'::uuid
FROM _pay_batch b;

\echo 'Payment posted ledger lines inserted'

-- Update payments
UPDATE ar.customer_payment p
SET journal_entry_id = b.journal_entry_id,
    posting_batch_id = b.batch_id
FROM _pay_batch b
WHERE p.payment_id = b.payment_id;

\echo 'Payments updated with journal references'

SELECT 'AR Payments remaining' AS check_name,
       count(*) AS count
FROM ar.customer_payment
WHERE journal_entry_id IS NULL
  AND status = 'CLEARED'
  AND amount != 0
  AND bank_account_id IS NOT NULL
  AND organization_id = :'org_id'::uuid;

DROP TABLE _pay_batch;

COMMIT;
\echo '=== PHASE 2 COMPLETE ==='


-- =============================================================================
-- PHASE 3: VERIFICATION
-- =============================================================================

\echo ''
\echo '=== VERIFICATION ==='

-- Check GL balance (debits should equal credits)
SELECT 'Journal entries balance check' AS check_name,
       SUM(total_debit_functional) AS total_debit,
       SUM(total_credit_functional) AS total_credit,
       SUM(total_debit_functional) - SUM(total_credit_functional) AS imbalance
FROM gl.journal_entry
WHERE journal_number LIKE 'BF-%';

-- Count records created
SELECT 'Total journal entries' AS metric, count(*) AS count FROM gl.journal_entry WHERE journal_number LIKE 'BF-%'
UNION ALL
SELECT 'Total journal lines', count(*) FROM gl.journal_entry_line jl JOIN gl.journal_entry je ON je.journal_entry_id = jl.journal_entry_id WHERE je.journal_number LIKE 'BF-%'
UNION ALL
SELECT 'Total posting batches', count(*) FROM gl.posting_batch pb WHERE pb.batch_description LIKE 'Backfill:%'
UNION ALL
SELECT 'Total posted ledger lines', count(*) FROM gl.posted_ledger_line pll JOIN gl.journal_entry je ON je.journal_entry_id = pll.journal_entry_id WHERE je.journal_number LIKE 'BF-%'
UNION ALL
SELECT 'Invoices now posted', count(*) FROM ar.invoice WHERE journal_entry_id IS NOT NULL AND posting_status = 'POSTED'
UNION ALL
SELECT 'Payments now posted', count(*) FROM ar.customer_payment WHERE journal_entry_id IS NOT NULL
ORDER BY 1;

\echo '=== BACKFILL COMPLETE ==='
