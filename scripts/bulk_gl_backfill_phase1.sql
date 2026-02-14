-- Phase 1: AR Invoices only (re-run after fixing orphans)
\set org_id '00000000-0000-0000-0000-000000000001'
\set backfill_user_id '00000000-0000-0000-0000-000000000000'

\echo '=== PHASE 1: AR INVOICES ==='
BEGIN;

CREATE TEMP TABLE _inv_batch AS
SELECT
    i.invoice_id, i.organization_id, i.invoice_date, i.currency_code,
    i.exchange_rate, i.total_amount, i.functional_currency_amount,
    i.invoice_number, i.correlation_id,
    c.ar_control_account_id, il.revenue_account_id,
    ar_acct.account_code AS ar_account_code,
    rev_acct.account_code AS revenue_account_code,
    c.legal_name AS customer_name,
    fp.fiscal_period_id,
    EXTRACT(YEAR FROM i.invoice_date)::int AS posting_year,
    gen_random_uuid() AS journal_entry_id,
    gen_random_uuid() AS je_line1_id,
    gen_random_uuid() AS je_line2_id,
    gen_random_uuid() AS batch_id,
    gen_random_uuid() AS pll_line1_id,
    gen_random_uuid() AS pll_line2_id,
    ROW_NUMBER() OVER (ORDER BY i.invoice_date, i.invoice_id) AS row_seq,
    CASE WHEN i.total_amount < 0 THEN true ELSE false END AS is_credit_note,
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

SELECT count(*) AS invoices_to_process FROM _inv_batch;

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
    b.journal_entry_id, b.organization_id,
    'BF-INV-' || LPAD(b.row_seq::text, 6, '0'),
    'STANDARD', b.invoice_date, b.invoice_date, b.fiscal_period_id,
    'AR Invoice ' || b.invoice_number || ' - ' || b.customer_name,
    b.invoice_number, b.currency_code, COALESCE(b.exchange_rate, 1.0),
    b.abs_amount, b.abs_amount, b.abs_functional, b.abs_functional,
    'POSTED', false, false,
    'AR', 'INVOICE', b.invoice_id,
    :'backfill_user_id'::uuid, :'backfill_user_id'::uuid, NOW(),
    :'backfill_user_id'::uuid, NOW(),
    :'backfill_user_id'::uuid, NOW(),
    b.batch_id, b.correlation_id, 1
FROM _inv_batch b;

\echo 'Journal entries inserted'

-- Line 1: Debit AR Control
INSERT INTO gl.journal_entry_line (
    line_id, journal_entry_id, line_number, account_id, description,
    debit_amount, credit_amount, debit_amount_functional, credit_amount_functional,
    currency_code, exchange_rate
)
SELECT b.je_line1_id, b.journal_entry_id, 1, b.ar_control_account_id,
    'AR Invoice: ' || b.customer_name,
    CASE WHEN NOT b.is_credit_note THEN b.abs_amount ELSE 0 END,
    CASE WHEN b.is_credit_note THEN b.abs_amount ELSE 0 END,
    CASE WHEN NOT b.is_credit_note THEN b.abs_functional ELSE 0 END,
    CASE WHEN b.is_credit_note THEN b.abs_functional ELSE 0 END,
    b.currency_code, COALESCE(b.exchange_rate, 1.0)
FROM _inv_batch b;

-- Line 2: Credit Revenue
INSERT INTO gl.journal_entry_line (
    line_id, journal_entry_id, line_number, account_id, description,
    debit_amount, credit_amount, debit_amount_functional, credit_amount_functional,
    currency_code, exchange_rate
)
SELECT b.je_line2_id, b.journal_entry_id, 2, b.revenue_account_id,
    'AR Invoice: ' || b.invoice_number,
    CASE WHEN b.is_credit_note THEN b.abs_amount ELSE 0 END,
    CASE WHEN NOT b.is_credit_note THEN b.abs_amount ELSE 0 END,
    CASE WHEN b.is_credit_note THEN b.abs_functional ELSE 0 END,
    CASE WHEN NOT b.is_credit_note THEN b.abs_functional ELSE 0 END,
    b.currency_code, COALESCE(b.exchange_rate, 1.0)
FROM _inv_batch b;

\echo 'Journal lines inserted'

-- Posting batches
INSERT INTO gl.posting_batch (
    batch_id, organization_id, fiscal_period_id, idempotency_key,
    source_module, batch_description,
    total_entries, posted_entries, failed_entries,
    status, submitted_by_user_id, submitted_at,
    processing_started_at, completed_at
)
SELECT b.batch_id, b.organization_id, b.fiscal_period_id,
    'ensure-gl-inv-' || b.invoice_id::text,
    'AR', 'Backfill: AR Invoice ' || b.invoice_number,
    2, 2, 0, 'POSTED',
    :'backfill_user_id'::uuid, NOW(), NOW(), NOW()
FROM _inv_batch b;

\echo 'Posting batches inserted'

-- Posted ledger lines (debit side)
INSERT INTO gl.posted_ledger_line (
    ledger_line_id, posting_year, organization_id,
    journal_entry_id, journal_line_id, posting_batch_id, fiscal_period_id,
    account_id, account_code, entry_date, posting_date,
    description, journal_reference,
    debit_amount, credit_amount,
    original_currency_code, original_debit_amount, original_credit_amount,
    exchange_rate, source_module, source_document_type, source_document_id,
    posted_by_user_id
)
SELECT b.pll_line1_id, b.posting_year, b.organization_id,
    b.journal_entry_id, b.je_line1_id, b.batch_id, b.fiscal_period_id,
    b.ar_control_account_id, b.ar_account_code,
    b.invoice_date, b.invoice_date,
    'AR Invoice: ' || b.customer_name, b.invoice_number,
    CASE WHEN NOT b.is_credit_note THEN b.abs_functional ELSE 0 END,
    CASE WHEN b.is_credit_note THEN b.abs_functional ELSE 0 END,
    b.currency_code,
    CASE WHEN NOT b.is_credit_note THEN b.abs_amount ELSE 0 END,
    CASE WHEN b.is_credit_note THEN b.abs_amount ELSE 0 END,
    COALESCE(b.exchange_rate, 1.0),
    'AR', 'INVOICE', b.invoice_id, :'backfill_user_id'::uuid
FROM _inv_batch b;

-- Posted ledger lines (credit side)
INSERT INTO gl.posted_ledger_line (
    ledger_line_id, posting_year, organization_id,
    journal_entry_id, journal_line_id, posting_batch_id, fiscal_period_id,
    account_id, account_code, entry_date, posting_date,
    description, journal_reference,
    debit_amount, credit_amount,
    original_currency_code, original_debit_amount, original_credit_amount,
    exchange_rate, source_module, source_document_type, source_document_id,
    posted_by_user_id
)
SELECT b.pll_line2_id, b.posting_year, b.organization_id,
    b.journal_entry_id, b.je_line2_id, b.batch_id, b.fiscal_period_id,
    b.revenue_account_id, b.revenue_account_code,
    b.invoice_date, b.invoice_date,
    'AR Invoice: ' || b.invoice_number, b.invoice_number,
    CASE WHEN b.is_credit_note THEN b.abs_functional ELSE 0 END,
    CASE WHEN NOT b.is_credit_note THEN b.abs_functional ELSE 0 END,
    b.currency_code,
    CASE WHEN b.is_credit_note THEN b.abs_amount ELSE 0 END,
    CASE WHEN NOT b.is_credit_note THEN b.abs_amount ELSE 0 END,
    COALESCE(b.exchange_rate, 1.0),
    'AR', 'INVOICE', b.invoice_id, :'backfill_user_id'::uuid
FROM _inv_batch b;

\echo 'Posted ledger lines inserted'

-- Update invoices
UPDATE ar.invoice i
SET journal_entry_id = b.journal_entry_id,
    posting_batch_id = b.batch_id,
    posting_status = 'POSTED'
FROM _inv_batch b
WHERE i.invoice_id = b.invoice_id;

\echo 'Invoices updated'

-- Verify
SELECT 'AR Invoices remaining (non-zero, postable)' AS check_name,
       count(*) AS count
FROM ar.invoice
WHERE journal_entry_id IS NULL
  AND status IN ('APPROVED', 'PAID', 'PARTIALLY_PAID', 'POSTED')
  AND total_amount != 0
  AND organization_id = :'org_id'::uuid;

DROP TABLE _inv_batch;

COMMIT;
\echo '=== PHASE 1 COMPLETE ==='
