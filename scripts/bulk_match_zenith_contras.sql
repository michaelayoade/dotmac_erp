-- Bulk match inter-Zenith contra entries (interbank transfers)
-- Creates GL journal entries for each transfer pair, then matches both sides.
-- All Zenith accounts share GL account 1200 (Zenith Bank), so journals are Dr 1200 / Cr 1200.
-- Idempotent: only processes unmatched lines.

BEGIN;

-- Step 1: Find matching debit/credit pairs across Zenith accounts
-- Use ROW_NUMBER to pick only the best match when ambiguous (prefer "TRF TO/FROM" descriptions)
CREATE TEMP TABLE _contra_pairs AS
WITH unmatched AS (
    SELECT sl.line_id, sl.transaction_date, sl.amount, sl.description, sl.transaction_type,
           ba.account_name, sl.statement_id, ba.gl_account_id,
           s.organization_id
    FROM banking.bank_statement_lines sl
    JOIN banking.bank_statements s ON sl.statement_id = s.statement_id
    JOIN banking.bank_accounts ba ON s.bank_account_id = ba.bank_account_id
    WHERE ba.account_name LIKE 'Zenith%'
      AND sl.is_matched = false
),
raw_pairs AS (
    SELECT d.line_id AS debit_line_id, c.line_id AS credit_line_id,
           d.account_name AS from_acct, c.account_name AS to_acct,
           d.transaction_date, d.amount,
           d.description AS debit_desc, c.description AS credit_desc,
           d.statement_id AS debit_stmt_id, c.statement_id AS credit_stmt_id,
           d.gl_account_id, d.organization_id,
           -- Score: prefer descriptions that mention "TRF TO"/"TRF FROM" matching
           CASE
               WHEN d.description ILIKE '%TRF TO%' AND c.description ILIKE '%TRF FROM%' THEN 100
               WHEN d.description ILIKE '%transfer%' AND c.description ILIKE '%transfer%' THEN 80
               ELSE 50
           END AS pair_score,
           -- Rank: pick best match per debit line
           ROW_NUMBER() OVER (PARTITION BY d.line_id ORDER BY
               CASE WHEN d.description ILIKE '%TRF TO%' AND c.description ILIKE '%TRF FROM%' THEN 0 ELSE 1 END,
               c.line_id
           ) AS debit_rank
    FROM unmatched d
    JOIN unmatched c ON d.transaction_date = c.transaction_date
                     AND ABS(d.amount - c.amount) < 0.50
                     AND d.transaction_type = 'debit'
                     AND c.transaction_type = 'credit'
                     AND d.account_name <> c.account_name
)
SELECT debit_line_id, credit_line_id, from_acct, to_acct,
       transaction_date, amount, debit_desc, credit_desc,
       debit_stmt_id, credit_stmt_id, gl_account_id, organization_id,
       -- Pre-generate UUIDs
       gen_random_uuid() AS journal_entry_id,
       gen_random_uuid() AS je_debit_line_id,
       gen_random_uuid() AS je_credit_line_id,
       gen_random_uuid() AS batch_id,
       gen_random_uuid() AS pll_debit_id,
       gen_random_uuid() AS pll_credit_id,
       gen_random_uuid() AS match1_id,
       gen_random_uuid() AS match2_id,
       ROW_NUMBER() OVER (ORDER BY transaction_date, debit_line_id) AS row_seq
FROM raw_pairs
WHERE debit_rank = 1
  -- Also ensure each credit line is used only once
  AND credit_line_id NOT IN (
      SELECT credit_line_id FROM raw_pairs WHERE debit_rank = 1
      GROUP BY credit_line_id HAVING count(*) > 1
  );

SELECT count(*) AS contra_pairs_to_process FROM _contra_pairs;

-- Step 2: Get fiscal periods and account code
CREATE TEMP TABLE _contra_enriched AS
SELECT cp.*,
       fp.fiscal_period_id,
       EXTRACT(YEAR FROM cp.transaction_date)::int AS posting_year,
       ga.account_code
FROM _contra_pairs cp
JOIN gl.fiscal_period fp ON cp.transaction_date BETWEEN fp.start_date AND fp.end_date
    AND fp.organization_id = cp.organization_id
JOIN gl.account ga ON ga.account_id = cp.gl_account_id;

-- Step 3: Create journal entries (contra: Dr 1200 / Cr 1200)
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
    b.journal_entry_id,
    b.organization_id,
    'BF-XFR-' || LPAD(b.row_seq::text, 6, '0'),
    'STANDARD',
    b.transaction_date,
    b.transaction_date,
    b.fiscal_period_id,
    'Inter-bank transfer: ' || b.from_acct || ' → ' || b.to_acct,
    'NGN',
    1.0,
    b.amount, b.amount, b.amount, b.amount,
    'POSTED',
    false, false,
    'BANKING', 'BANK_TRANSFER',
    '00000000-0000-0000-0000-000000000000'::uuid, '00000000-0000-0000-0000-000000000000'::uuid, NOW(),
    '00000000-0000-0000-0000-000000000000'::uuid, NOW(),
    '00000000-0000-0000-0000-000000000000'::uuid, NOW(),
    b.batch_id,
    1
FROM _contra_enriched b;

-- Step 4: Journal lines
-- Line 1: Debit (money INTO destination account)
INSERT INTO gl.journal_entry_line (
    line_id, journal_entry_id, line_number, account_id, description,
    debit_amount, credit_amount, debit_amount_functional, credit_amount_functional,
    currency_code, exchange_rate
)
SELECT
    b.je_debit_line_id, b.journal_entry_id, 1, b.gl_account_id,
    'Transfer in from ' || b.from_acct,
    b.amount, 0, b.amount, 0,
    'NGN', 1.0
FROM _contra_enriched b;

-- Line 2: Credit (money OUT of source account)
INSERT INTO gl.journal_entry_line (
    line_id, journal_entry_id, line_number, account_id, description,
    debit_amount, credit_amount, debit_amount_functional, credit_amount_functional,
    currency_code, exchange_rate
)
SELECT
    b.je_credit_line_id, b.journal_entry_id, 2, b.gl_account_id,
    'Transfer out to ' || b.to_acct,
    0, b.amount, 0, b.amount,
    'NGN', 1.0
FROM _contra_enriched b;

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
    'contra-xfr-' || b.debit_line_id::text,
    'BANKING',
    'Backfill: Inter-bank transfer ' || b.from_acct || ' → ' || b.to_acct,
    2, 2, 0,
    'POSTED',
    '00000000-0000-0000-0000-000000000000'::uuid, NOW(), NOW(), NOW()
FROM _contra_enriched b;

-- Step 6: Posted ledger lines (debit side)
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
    b.gl_account_id, b.account_code,
    b.transaction_date, b.transaction_date,
    'Transfer in from ' || b.from_acct,
    b.amount, 0, 'NGN', b.amount, 0, 1.0,
    'BANKING', 'BANK_TRANSFER',
    '00000000-0000-0000-0000-000000000000'::uuid
FROM _contra_enriched b;

-- Posted ledger lines (credit side)
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
    b.gl_account_id, b.account_code,
    b.transaction_date, b.transaction_date,
    'Transfer out to ' || b.to_acct,
    0, b.amount, 'NGN', 0, b.amount, 1.0,
    'BANKING', 'BANK_TRANSFER',
    '00000000-0000-0000-0000-000000000000'::uuid
FROM _contra_enriched b;

-- Step 7: Match statement lines
-- Debit statement line (money left source) → matched to Credit journal line
INSERT INTO banking.bank_statement_line_matches (
    match_id, statement_line_id, journal_line_id, match_score, matched_at, is_primary
)
SELECT b.match1_id, b.debit_line_id, b.je_credit_line_id, 100, NOW(), true
FROM _contra_enriched b;

-- Credit statement line (money entered dest) → matched to Debit journal line
INSERT INTO banking.bank_statement_line_matches (
    match_id, statement_line_id, journal_line_id, match_score, matched_at, is_primary
)
SELECT b.match2_id, b.credit_line_id, b.je_debit_line_id, 100, NOW(), true
FROM _contra_enriched b;

-- Step 8: Mark statement lines as matched
UPDATE banking.bank_statement_lines sl
SET is_matched = true,
    matched_journal_line_id = b.je_credit_line_id
FROM _contra_enriched b
WHERE sl.line_id = b.debit_line_id;

UPDATE banking.bank_statement_lines sl
SET is_matched = true,
    matched_journal_line_id = b.je_debit_line_id
FROM _contra_enriched b
WHERE sl.line_id = b.credit_line_id;

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
SELECT 'Contra pairs matched' AS status, count(*) AS count FROM _contra_enriched;

SELECT ba.account_name,
       s.matched_lines, s.unmatched_lines,
       ROUND(100.0 * s.matched_lines / NULLIF(s.matched_lines + s.unmatched_lines, 0), 1) AS match_pct
FROM banking.bank_statements s
JOIN banking.bank_accounts ba ON s.bank_account_id = ba.bank_account_id
WHERE ba.account_name LIKE 'Zenith%'
ORDER BY ba.account_name, s.period_start;

DROP TABLE _contra_pairs;
DROP TABLE _contra_enriched;

COMMIT;
