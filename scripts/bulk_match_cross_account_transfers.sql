-- Bulk match cross-account bank transfers
-- Handles: Zenith ↔ UBA, Zenith/UBA → Paystack (Collections & OPEX), Paystack OPEX → Zenith/UBA
-- Creates GL journal entries (Dr destination GL, Cr source GL) and matches both sides.
-- Idempotent: only processes unmatched lines.

BEGIN;

-- Step 1: Find matching debit/credit pairs across ALL bank accounts
CREATE TEMP TABLE _xfer_pairs AS
WITH unmatched AS (
    SELECT sl.line_id, sl.transaction_date, sl.amount, sl.description, sl.transaction_type,
           ba.account_name, sl.statement_id, ba.gl_account_id,
           s.organization_id
    FROM banking.bank_statement_lines sl
    JOIN banking.bank_statements s ON sl.statement_id = s.statement_id
    JOIN banking.bank_accounts ba ON s.bank_account_id = ba.bank_account_id
    WHERE sl.is_matched = false
      AND ba.account_name IN (
          'Zenith 461','Zenith 523','Zenith 454 (Int Project)','Zenith USD',
          'UBA 96 (Main)','UBA USD',
          'Paystack Collections','Paystack OPEX'
      )
),
raw_pairs AS (
    SELECT d.line_id AS debit_line_id, c.line_id AS credit_line_id,
           d.account_name AS from_acct, c.account_name AS to_acct,
           d.transaction_date, d.amount,
           d.description AS debit_desc, c.description AS credit_desc,
           d.statement_id AS debit_stmt_id, c.statement_id AS credit_stmt_id,
           d.gl_account_id AS source_gl_account_id,
           c.gl_account_id AS dest_gl_account_id,
           d.organization_id,
           -- Score: prefer matching transfer descriptions
           CASE
               WHEN d.description ILIKE '%TRF TO%' AND c.description ILIKE '%TRF FROM%' THEN 100
               WHEN d.description ILIKE '%transfer%' AND c.description ILIKE '%transfer%' THEN 90
               WHEN d.description ILIKE '%settlement%' AND c.description ILIKE '%TNF-%' THEN 85
               WHEN d.description ILIKE '%settlement%' AND c.description ILIKE '%settlement%' THEN 80
               ELSE 50
           END AS pair_score,
           ROW_NUMBER() OVER (PARTITION BY d.line_id ORDER BY
               CASE
                   WHEN d.description ILIKE '%TRF TO%' AND c.description ILIKE '%TRF FROM%' THEN 0
                   WHEN d.description ILIKE '%transfer%' AND c.description ILIKE '%transfer%' THEN 1
                   WHEN d.description ILIKE '%settlement%' THEN 2
                   ELSE 3
               END,
               c.line_id
           ) AS debit_rank
    FROM unmatched d
    JOIN unmatched c ON d.transaction_date = c.transaction_date
                     AND ABS(d.amount - c.amount) < 0.50
                     AND d.transaction_type = 'debit'
                     AND c.transaction_type = 'credit'
                     AND d.account_name <> c.account_name
                     -- Exclude same-GL same-bank contras (already handled)
                     AND NOT (d.gl_account_id = c.gl_account_id
                              AND d.account_name LIKE 'Zenith%'
                              AND c.account_name LIKE 'Zenith%')
)
SELECT debit_line_id, credit_line_id, from_acct, to_acct,
       transaction_date, amount, debit_desc, credit_desc,
       debit_stmt_id, credit_stmt_id,
       source_gl_account_id, dest_gl_account_id,
       organization_id,
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
  AND credit_line_id NOT IN (
      SELECT credit_line_id FROM raw_pairs WHERE debit_rank = 1
      GROUP BY credit_line_id HAVING count(*) > 1
  );

SELECT count(*) AS cross_account_pairs FROM _xfer_pairs;
SELECT from_acct, to_acct, count(*) AS cnt FROM _xfer_pairs GROUP BY from_acct, to_acct ORDER BY cnt DESC;

-- Step 2: Enrich with fiscal periods and account codes
CREATE TEMP TABLE _xfer_enriched AS
SELECT xp.*,
       fp.fiscal_period_id,
       EXTRACT(YEAR FROM xp.transaction_date)::int AS posting_year,
       src_ga.account_code AS source_account_code,
       dst_ga.account_code AS dest_account_code,
       src_ga.account_name AS source_account_name,
       dst_ga.account_name AS dest_account_name
FROM _xfer_pairs xp
JOIN gl.fiscal_period fp ON xp.transaction_date BETWEEN fp.start_date AND fp.end_date
    AND fp.organization_id = xp.organization_id
JOIN gl.account src_ga ON src_ga.account_id = xp.source_gl_account_id
JOIN gl.account dst_ga ON dst_ga.account_id = xp.dest_gl_account_id;

SELECT count(*) AS enriched_count FROM _xfer_enriched;

-- Step 3: Create journal entries
-- For a transfer FROM source TO destination:
--   Debit: destination GL account (money arrived)
--   Credit: source GL account (money left)
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
    'BF-XFR-' || LPAD((b.row_seq + 261)::text, 6, '0'),  -- offset past Zenith contras
    'STANDARD',
    b.transaction_date, b.transaction_date, b.fiscal_period_id,
    'Bank transfer: ' || b.from_acct || ' → ' || b.to_acct,
    'NGN', 1.0,
    b.amount, b.amount, b.amount, b.amount,
    'POSTED', false, false,
    'BANKING', 'BANK_TRANSFER',
    '00000000-0000-0000-0000-000000000000'::uuid, '00000000-0000-0000-0000-000000000000'::uuid, NOW(),
    '00000000-0000-0000-0000-000000000000'::uuid, NOW(),
    '00000000-0000-0000-0000-000000000000'::uuid, NOW(),
    b.batch_id, 1
FROM _xfer_enriched b;

-- Step 4: Journal lines
-- Line 1: Debit destination GL (money IN to destination bank)
INSERT INTO gl.journal_entry_line (
    line_id, journal_entry_id, line_number, account_id, description,
    debit_amount, credit_amount, debit_amount_functional, credit_amount_functional,
    currency_code, exchange_rate
)
SELECT
    b.je_debit_line_id, b.journal_entry_id, 1, b.dest_gl_account_id,
    'Transfer in from ' || b.from_acct,
    b.amount, 0, b.amount, 0, 'NGN', 1.0
FROM _xfer_enriched b;

-- Line 2: Credit source GL (money OUT of source bank)
INSERT INTO gl.journal_entry_line (
    line_id, journal_entry_id, line_number, account_id, description,
    debit_amount, credit_amount, debit_amount_functional, credit_amount_functional,
    currency_code, exchange_rate
)
SELECT
    b.je_credit_line_id, b.journal_entry_id, 2, b.source_gl_account_id,
    'Transfer out to ' || b.to_acct,
    0, b.amount, 0, b.amount, 'NGN', 1.0
FROM _xfer_enriched b;

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
    'xfer-' || b.debit_line_id::text,
    'BANKING',
    'Backfill: Transfer ' || b.from_acct || ' → ' || b.to_acct,
    2, 2, 0, 'POSTED',
    '00000000-0000-0000-0000-000000000000'::uuid, NOW(), NOW(), NOW()
FROM _xfer_enriched b;

-- Step 6: Posted ledger lines
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
    'Transfer in from ' || b.from_acct,
    b.amount, 0, 'NGN', b.amount, 0, 1.0,
    'BANKING', 'BANK_TRANSFER',
    '00000000-0000-0000-0000-000000000000'::uuid
FROM _xfer_enriched b;

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
    b.source_gl_account_id, b.source_account_code,
    b.transaction_date, b.transaction_date,
    'Transfer out to ' || b.to_acct,
    0, b.amount, 'NGN', 0, b.amount, 1.0,
    'BANKING', 'BANK_TRANSFER',
    '00000000-0000-0000-0000-000000000000'::uuid
FROM _xfer_enriched b;

-- Step 7: Match statement lines
-- Source debit line → credit journal line (money leaving source)
INSERT INTO banking.bank_statement_line_matches (
    match_id, statement_line_id, journal_line_id, match_score, matched_at, is_primary
)
SELECT b.match1_id, b.debit_line_id, b.je_credit_line_id, 100, NOW(), true
FROM _xfer_enriched b;

-- Destination credit line → debit journal line (money entering destination)
INSERT INTO banking.bank_statement_line_matches (
    match_id, statement_line_id, journal_line_id, match_score, matched_at, is_primary
)
SELECT b.match2_id, b.credit_line_id, b.je_debit_line_id, 100, NOW(), true
FROM _xfer_enriched b;

-- Step 8: Mark statement lines as matched
UPDATE banking.bank_statement_lines sl
SET is_matched = true,
    matched_journal_line_id = b.je_credit_line_id
FROM _xfer_enriched b
WHERE sl.line_id = b.debit_line_id;

UPDATE banking.bank_statement_lines sl
SET is_matched = true,
    matched_journal_line_id = b.je_debit_line_id
FROM _xfer_enriched b
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
SELECT ba.account_name,
       s.matched_lines, s.unmatched_lines,
       ROUND(100.0 * s.matched_lines / NULLIF(s.matched_lines + s.unmatched_lines, 0), 1) AS match_pct
FROM banking.bank_statements s
JOIN banking.bank_accounts ba ON s.bank_account_id = ba.bank_account_id
ORDER BY ba.account_name, s.period_start;

DROP TABLE _xfer_pairs;
DROP TABLE _xfer_enriched;

COMMIT;
