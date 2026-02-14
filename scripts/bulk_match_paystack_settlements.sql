-- Bulk match Paystack settlement credits in destination banks
-- These are credits arriving in Zenith 461/523 and UBA from Paystack that have no
-- corresponding GL entry. Creates a journal (Dr destination bank GL / Cr Paystack GL)
-- for each credit and matches it.
-- Idempotent: only processes unmatched lines.

BEGIN;

-- Step 1: Find unmatched Paystack-tagged credits in destination banks
CREATE TEMP TABLE _ps_credits AS
SELECT sl.line_id, sl.transaction_date, sl.amount, sl.description,
       sl.statement_id,
       ba.account_name, ba.gl_account_id AS dest_gl_account_id,
       s.organization_id,
       gen_random_uuid() AS journal_entry_id,
       gen_random_uuid() AS je_debit_line_id,
       gen_random_uuid() AS je_credit_line_id,
       gen_random_uuid() AS batch_id,
       gen_random_uuid() AS pll_debit_id,
       gen_random_uuid() AS pll_credit_id,
       gen_random_uuid() AS match_id,
       ROW_NUMBER() OVER (ORDER BY sl.transaction_date, sl.line_id) AS row_seq
FROM banking.bank_statement_lines sl
JOIN banking.bank_statements s ON sl.statement_id = s.statement_id
JOIN banking.bank_accounts ba ON s.bank_account_id = ba.bank_account_id
WHERE ba.account_name IN ('Zenith 461', 'Zenith 523', 'UBA 96 (Main)')
  AND sl.transaction_type = 'credit'
  AND sl.is_matched = false
  AND (sl.description ILIKE '%paystack%'
    OR sl.description ILIKE '%PSSTx%'
    OR sl.description ILIKE '%PSTK_Stlmt%');

SELECT count(*) AS paystack_credits_to_match FROM _ps_credits;
SELECT account_name, count(*) AS cnt, sum(amount)::numeric(15,2) AS total
FROM _ps_credits GROUP BY account_name ORDER BY account_name;

-- Step 2: Enrich with fiscal periods, account codes, and Paystack GL account
CREATE TEMP TABLE _ps_enriched AS
SELECT pc.*,
       fp.fiscal_period_id,
       EXTRACT(YEAR FROM pc.transaction_date)::int AS posting_year,
       dst_ga.account_code AS dest_account_code,
       dst_ga.account_name AS dest_account_name,
       ps_ga.account_id AS paystack_gl_account_id,
       ps_ga.account_code AS paystack_account_code,
       ps_ga.account_name AS paystack_account_name
FROM _ps_credits pc
JOIN gl.fiscal_period fp ON pc.transaction_date BETWEEN fp.start_date AND fp.end_date
    AND fp.organization_id = pc.organization_id
JOIN gl.account dst_ga ON dst_ga.account_id = pc.dest_gl_account_id
-- Paystack GL = account_code 1210 (Paystack Collections)
CROSS JOIN (
    SELECT account_id, account_code, account_name
    FROM gl.account WHERE account_code = '1210' LIMIT 1
) ps_ga;

SELECT count(*) AS enriched_count FROM _ps_enriched;

-- Step 3: Create journal entries
-- For a Paystack settlement credit arriving in the bank:
--   Debit: destination bank GL account (money arrived)
--   Credit: Paystack GL account (settlement from Paystack)
INSERT INTO gl.journal_entry (
    journal_entry_id, organization_id, journal_number, journal_type,
    entry_date, posting_date, fiscal_period_id,
    description, currency_code, exchange_rate,
    total_debit, total_credit, total_debit_functional, total_credit_functional,
    status, is_reversal, is_intercompany,
    source_module, source_document_type,
    created_by_user_id, submitted_by_user_id, submitted_at,
    approved_by_user_id, approved_at,
    posted_by_user_id, posted_at,
    posting_batch_id, version
)
SELECT
    b.journal_entry_id, b.organization_id,
    'BF-PST-' || LPAD(b.row_seq::text, 6, '0'),
    'STANDARD',
    b.transaction_date, b.transaction_date, b.fiscal_period_id,
    'Paystack settlement: ' || b.account_name || ' ← Paystack',
    'NGN', 1.0,
    b.amount, b.amount, b.amount, b.amount,
    'POSTED', false, false,
    'BANKING', 'PAYSTACK_SETTLEMENT',
    '00000000-0000-0000-0000-000000000000'::uuid, '00000000-0000-0000-0000-000000000000'::uuid, NOW(),
    '00000000-0000-0000-0000-000000000000'::uuid, NOW(),
    '00000000-0000-0000-0000-000000000000'::uuid, NOW(),
    b.batch_id, 1
FROM _ps_enriched b;

-- Step 4: Journal lines
-- Line 1: Debit destination bank GL (money arrived in bank)
INSERT INTO gl.journal_entry_line (
    line_id, journal_entry_id, line_number, account_id, description,
    debit_amount, credit_amount, debit_amount_functional, credit_amount_functional,
    currency_code, exchange_rate
)
SELECT
    b.je_debit_line_id, b.journal_entry_id, 1, b.dest_gl_account_id,
    'Paystack settlement deposit: ' || LEFT(b.description, 60),
    b.amount, 0, b.amount, 0, 'NGN', 1.0
FROM _ps_enriched b;

-- Line 2: Credit Paystack GL (settlement from Paystack)
INSERT INTO gl.journal_entry_line (
    line_id, journal_entry_id, line_number, account_id, description,
    debit_amount, credit_amount, debit_amount_functional, credit_amount_functional,
    currency_code, exchange_rate
)
SELECT
    b.je_credit_line_id, b.journal_entry_id, 2, b.paystack_gl_account_id,
    'Paystack settlement to ' || b.account_name,
    0, b.amount, 0, b.amount, 'NGN', 1.0
FROM _ps_enriched b;

-- Step 5: Posting batches
INSERT INTO gl.posting_batch (
    batch_id, organization_id, fiscal_period_id, idempotency_key,
    source_module, batch_description,
    total_entries, posted_entries, failed_entries,
    status, submitted_by_user_id, submitted_at,
    processing_started_at, completed_at
)
SELECT
    b.batch_id, b.organization_id, b.fiscal_period_id,
    'pst-' || b.line_id::text,
    'BANKING',
    'Backfill: Paystack settlement ' || b.account_name,
    2, 2, 0, 'POSTED',
    '00000000-0000-0000-0000-000000000000'::uuid, NOW(), NOW(), NOW()
FROM _ps_enriched b;

-- Step 6: Posted ledger lines
-- Debit side (destination bank)
INSERT INTO gl.posted_ledger_line (
    ledger_line_id, posting_year, organization_id,
    journal_entry_id, journal_line_id, posting_batch_id, fiscal_period_id,
    account_id, account_code, entry_date, posting_date,
    description, debit_amount, credit_amount,
    original_currency_code, original_debit_amount, original_credit_amount,
    exchange_rate, source_module, source_document_type,
    posted_by_user_id
)
SELECT
    b.pll_debit_id, b.posting_year, b.organization_id,
    b.journal_entry_id, b.je_debit_line_id, b.batch_id, b.fiscal_period_id,
    b.dest_gl_account_id, b.dest_account_code,
    b.transaction_date, b.transaction_date,
    'Paystack settlement deposit: ' || LEFT(b.description, 60),
    b.amount, 0, 'NGN', b.amount, 0, 1.0,
    'BANKING', 'PAYSTACK_SETTLEMENT',
    '00000000-0000-0000-0000-000000000000'::uuid
FROM _ps_enriched b;

-- Credit side (Paystack GL)
INSERT INTO gl.posted_ledger_line (
    ledger_line_id, posting_year, organization_id,
    journal_entry_id, journal_line_id, posting_batch_id, fiscal_period_id,
    account_id, account_code, entry_date, posting_date,
    description, debit_amount, credit_amount,
    original_currency_code, original_debit_amount, original_credit_amount,
    exchange_rate, source_module, source_document_type,
    posted_by_user_id
)
SELECT
    b.pll_credit_id, b.posting_year, b.organization_id,
    b.journal_entry_id, b.je_credit_line_id, b.batch_id, b.fiscal_period_id,
    b.paystack_gl_account_id, b.paystack_account_code,
    b.transaction_date, b.transaction_date,
    'Paystack settlement to ' || b.account_name,
    0, b.amount, 'NGN', 0, b.amount, 1.0,
    'BANKING', 'PAYSTACK_SETTLEMENT',
    '00000000-0000-0000-0000-000000000000'::uuid
FROM _ps_enriched b;

-- Step 7: Match statement lines to the debit journal line (money entering bank)
INSERT INTO banking.bank_statement_line_matches (
    match_id, statement_line_id, journal_line_id, match_score, matched_at, is_primary
)
SELECT b.match_id, b.line_id, b.je_debit_line_id, 100, NOW(), true
FROM _ps_enriched b;

-- Step 8: Mark statement lines as matched
UPDATE banking.bank_statement_lines sl
SET is_matched = true,
    matched_journal_line_id = b.je_debit_line_id
FROM _ps_enriched b
WHERE sl.line_id = b.line_id;

-- Step 9: Fix statement counters
UPDATE banking.bank_statements s
SET matched_lines = sub.matched,
    unmatched_lines = sub.unmatched
FROM (
    SELECT sl.statement_id,
           count(*) FILTER (WHERE sl.is_matched = true) AS matched,
           count(*) FILTER (WHERE sl.is_matched = false) AS unmatched
    FROM banking.bank_statement_lines sl
    GROUP BY sl.statement_id
) sub
WHERE s.statement_id = sub.statement_id
  AND (s.matched_lines <> sub.matched OR s.unmatched_lines <> sub.unmatched);

-- Verification
SELECT 'Paystack credits matched' AS check_type,
       ba.account_name,
       count(*) FILTER (WHERE sl.is_matched) AS matched,
       count(*) FILTER (WHERE NOT sl.is_matched AND (
           sl.description ILIKE '%paystack%' OR sl.description ILIKE '%PSSTx%'
       )) AS still_unmatched_paystack,
       count(*) FILTER (WHERE NOT sl.is_matched) AS total_unmatched
FROM banking.bank_statement_lines sl
JOIN banking.bank_statements s ON sl.statement_id = s.statement_id
JOIN banking.bank_accounts ba ON s.bank_account_id = ba.bank_account_id
WHERE ba.account_name IN ('Zenith 461', 'Zenith 523', 'UBA 96 (Main)')
  AND sl.transaction_type = 'credit'
GROUP BY ba.account_name
ORDER BY ba.account_name;

DROP TABLE _ps_credits;
DROP TABLE _ps_enriched;

COMMIT;
