-- Bulk match UBA ↔ Zenith inter-bank transfers with NIP fee capture
-- Creates 3-line GL journals: Dr Dest Bank + Dr Finance Cost (NIP fee) + Cr Source Bank
-- NIP fee tiers: 10.75 (≤5K), 26.88 (5K-50K), 53.75 (>50K), 0.00 (exact)
-- Idempotent: only processes unmatched lines.

BEGIN;

-- =========================================================================
-- PART A: UBA → Zenith (UBA debit includes NIP fee, Zenith credit = net)
-- =========================================================================

CREATE TEMP TABLE _uba_to_zenith AS
WITH uba_debits AS (
    SELECT sl.line_id, sl.transaction_date, sl.amount, sl.description,
           sl.statement_id, s.organization_id, ba.gl_account_id
    FROM banking.bank_statement_lines sl
    JOIN banking.bank_statements s ON sl.statement_id = s.statement_id
    JOIN banking.bank_accounts ba ON s.bank_account_id = ba.bank_account_id
    WHERE ba.account_name = 'UBA 96 (Main)'
      AND sl.is_matched = false AND sl.transaction_type = 'debit'
),
zenith_credits AS (
    SELECT sl.line_id, sl.transaction_date, sl.amount, sl.description,
           sl.statement_id, ba.account_name, ba.gl_account_id
    FROM banking.bank_statement_lines sl
    JOIN banking.bank_statements s ON sl.statement_id = s.statement_id
    JOIN banking.bank_accounts ba ON s.bank_account_id = ba.bank_account_id
    WHERE ba.account_name LIKE 'Zenith%'
      AND sl.is_matched = false AND sl.transaction_type = 'credit'
),
raw_pairs AS (
    SELECT u.line_id AS uba_line_id, z.line_id AS zenith_line_id,
           'UBA 96 (Main)' AS from_acct, z.account_name AS to_acct,
           u.transaction_date, u.amount AS uba_amount, z.amount AS zenith_amount,
           ROUND((u.amount - z.amount)::numeric, 2) AS nip_fee,
           u.description AS uba_desc, z.description AS zenith_desc,
           u.statement_id AS uba_stmt_id, z.statement_id AS zenith_stmt_id,
           u.gl_account_id AS uba_gl_id, z.gl_account_id AS zenith_gl_id,
           u.organization_id,
           ROW_NUMBER() OVER (PARTITION BY u.line_id ORDER BY z.line_id) AS rank_per_debit
    FROM uba_debits u
    JOIN zenith_credits z ON u.transaction_date = z.transaction_date
      AND ROUND((u.amount - z.amount)::numeric, 2) IN (10.75, 26.88, 53.75, 0.00)
)
SELECT *, gen_random_uuid() AS journal_entry_id,
       gen_random_uuid() AS je_debit_line_id,   -- Dr Zenith (principal)
       gen_random_uuid() AS je_fee_line_id,      -- Dr Finance Cost (NIP fee)
       gen_random_uuid() AS je_credit_line_id,   -- Cr UBA (total)
       gen_random_uuid() AS batch_id,
       gen_random_uuid() AS pll_debit_id,
       gen_random_uuid() AS pll_fee_id,
       gen_random_uuid() AS pll_credit_id,
       gen_random_uuid() AS match_uba_id,
       gen_random_uuid() AS match_zenith_id,
       ROW_NUMBER() OVER (ORDER BY transaction_date, uba_line_id) AS row_seq
FROM raw_pairs
WHERE rank_per_debit = 1
  AND zenith_line_id NOT IN (
      SELECT zenith_line_id FROM raw_pairs WHERE rank_per_debit = 1
      GROUP BY zenith_line_id HAVING count(*) > 1
  );

SELECT 'UBA → Zenith pairs' AS flow, count(*) AS cnt FROM _uba_to_zenith;

-- =========================================================================
-- PART B: Zenith → UBA (exact amount matches only — Zenith NIP fees are
--         separate statement lines handled by bank charge rules)
-- =========================================================================

CREATE TEMP TABLE _zenith_to_uba AS
WITH zenith_debits AS (
    SELECT sl.line_id, sl.transaction_date, sl.amount, sl.description,
           sl.statement_id, s.organization_id, ba.account_name, ba.gl_account_id
    FROM banking.bank_statement_lines sl
    JOIN banking.bank_statements s ON sl.statement_id = s.statement_id
    JOIN banking.bank_accounts ba ON s.bank_account_id = ba.bank_account_id
    WHERE ba.account_name LIKE 'Zenith%'
      AND sl.is_matched = false AND sl.transaction_type = 'debit'
),
uba_credits AS (
    SELECT sl.line_id, sl.transaction_date, sl.amount, sl.description,
           sl.statement_id, ba.gl_account_id
    FROM banking.bank_statement_lines sl
    JOIN banking.bank_statements s ON sl.statement_id = s.statement_id
    JOIN banking.bank_accounts ba ON s.bank_account_id = ba.bank_account_id
    WHERE ba.account_name = 'UBA 96 (Main)'
      AND sl.is_matched = false AND sl.transaction_type = 'credit'
),
raw_pairs AS (
    SELECT z.line_id AS zenith_line_id, u.line_id AS uba_line_id,
           z.account_name AS from_acct, 'UBA 96 (Main)' AS to_acct,
           z.transaction_date, z.amount AS zenith_amount, u.amount AS uba_amount,
           ROUND((z.amount - u.amount)::numeric, 2) AS nip_fee,
           z.description AS zenith_desc, u.description AS uba_desc,
           z.statement_id AS zenith_stmt_id, u.statement_id AS uba_stmt_id,
           z.gl_account_id AS zenith_gl_id, u.gl_account_id AS uba_gl_id,
           z.organization_id,
           ROW_NUMBER() OVER (PARTITION BY z.line_id ORDER BY u.line_id) AS rank_per_debit
    FROM zenith_debits z
    JOIN uba_credits u ON z.transaction_date = u.transaction_date
      AND ROUND((z.amount - u.amount)::numeric, 2) IN (10.75, 26.88, 53.75, 0.00)
)
SELECT *, gen_random_uuid() AS journal_entry_id,
       gen_random_uuid() AS je_debit_line_id,
       gen_random_uuid() AS je_fee_line_id,
       gen_random_uuid() AS je_credit_line_id,
       gen_random_uuid() AS batch_id,
       gen_random_uuid() AS pll_debit_id,
       gen_random_uuid() AS pll_fee_id,
       gen_random_uuid() AS pll_credit_id,
       gen_random_uuid() AS match_zenith_id,
       gen_random_uuid() AS match_uba_id,
       ROW_NUMBER() OVER (ORDER BY transaction_date, zenith_line_id) AS row_seq
FROM raw_pairs
WHERE rank_per_debit = 1
  AND uba_line_id NOT IN (
      SELECT uba_line_id FROM raw_pairs WHERE rank_per_debit = 1
      GROUP BY uba_line_id HAVING count(*) > 1
  );

SELECT 'Zenith → UBA pairs' AS flow, count(*) AS cnt FROM _zenith_to_uba;

-- =========================================================================
-- PART C: Create GL journals for UBA → Zenith
-- =========================================================================

-- Enrich with fiscal periods
CREATE TEMP TABLE _u2z AS
SELECT t.*,
       fp.fiscal_period_id,
       EXTRACT(YEAR FROM t.transaction_date)::int AS posting_year,
       src.account_code AS uba_account_code,
       dst.account_code AS zenith_account_code
FROM _uba_to_zenith t
JOIN gl.fiscal_period fp ON t.transaction_date BETWEEN fp.start_date AND fp.end_date
    AND fp.organization_id = t.organization_id
JOIN gl.account src ON src.account_id = t.uba_gl_id
JOIN gl.account dst ON dst.account_id = t.zenith_gl_id;

-- Journal entries
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
    j.journal_entry_id, j.organization_id,
    'BF-UBZ-' || LPAD(j.row_seq::text, 6, '0'),
    'STANDARD',
    j.transaction_date, j.transaction_date, j.fiscal_period_id,
    'UBA → ' || j.to_acct || ' transfer (NIP fee: ' || j.nip_fee || ')',
    'NGN', 1.0,
    j.uba_amount, j.uba_amount, j.uba_amount, j.uba_amount,
    'POSTED', false, false,
    'BANKING', 'BANK_TRANSFER',
    '00000000-0000-0000-0000-000000000000'::uuid, '00000000-0000-0000-0000-000000000000'::uuid, NOW(),
    '00000000-0000-0000-0000-000000000000'::uuid, NOW(),
    '00000000-0000-0000-0000-000000000000'::uuid, NOW(),
    j.batch_id, 1
FROM _u2z j;

-- Line 1: Dr Zenith GL (principal received)
INSERT INTO gl.journal_entry_line (
    line_id, journal_entry_id, line_number, account_id, description,
    debit_amount, credit_amount, debit_amount_functional, credit_amount_functional,
    currency_code, exchange_rate
)
SELECT j.je_debit_line_id, j.journal_entry_id, 1, j.zenith_gl_id,
       'Transfer in from UBA',
       j.zenith_amount, 0, j.zenith_amount, 0, 'NGN', 1.0
FROM _u2z j;

-- Line 2: Dr Finance Cost (NIP fee) — skip if fee = 0
INSERT INTO gl.journal_entry_line (
    line_id, journal_entry_id, line_number, account_id, description,
    debit_amount, credit_amount, debit_amount_functional, credit_amount_functional,
    currency_code, exchange_rate
)
SELECT j.je_fee_line_id, j.journal_entry_id, 2,
       'e3b904ab-57bd-4429-95a8-a1438ae4ecca'::uuid,  -- 6080 Finance Cost
       'NIP transfer fee (UBA → ' || j.to_acct || ')',
       j.nip_fee, 0, j.nip_fee, 0, 'NGN', 1.0
FROM _u2z j
WHERE j.nip_fee > 0;

-- Line 3: Cr UBA GL (total debited)
INSERT INTO gl.journal_entry_line (
    line_id, journal_entry_id, line_number, account_id, description,
    debit_amount, credit_amount, debit_amount_functional, credit_amount_functional,
    currency_code, exchange_rate
)
SELECT j.je_credit_line_id, j.journal_entry_id, 3, j.uba_gl_id,
       'Transfer out to ' || j.to_acct,
       0, j.uba_amount, 0, j.uba_amount, 'NGN', 1.0
FROM _u2z j;

-- Posting batches
INSERT INTO gl.posting_batch (
    batch_id, organization_id, fiscal_period_id, idempotency_key,
    source_module, batch_description,
    total_entries, posted_entries, failed_entries,
    status, submitted_by_user_id, submitted_at,
    processing_started_at, completed_at
)
SELECT j.batch_id, j.organization_id, j.fiscal_period_id,
       'ubz-' || j.uba_line_id::text, 'BANKING',
       'Backfill: UBA → ' || j.to_acct || ' transfer',
       CASE WHEN j.nip_fee > 0 THEN 3 ELSE 2 END,
       CASE WHEN j.nip_fee > 0 THEN 3 ELSE 2 END, 0,
       'POSTED', '00000000-0000-0000-0000-000000000000'::uuid, NOW(), NOW(), NOW()
FROM _u2z j;

-- Posted ledger lines: Zenith debit
INSERT INTO gl.posted_ledger_line (
    ledger_line_id, posting_year, organization_id,
    journal_entry_id, journal_line_id, posting_batch_id, fiscal_period_id,
    account_id, account_code, entry_date, posting_date,
    description, debit_amount, credit_amount,
    original_currency_code, original_debit_amount, original_credit_amount,
    exchange_rate, source_module, source_document_type, posted_by_user_id
)
SELECT j.pll_debit_id, j.posting_year, j.organization_id,
       j.journal_entry_id, j.je_debit_line_id, j.batch_id, j.fiscal_period_id,
       j.zenith_gl_id, j.zenith_account_code,
       j.transaction_date, j.transaction_date,
       'Transfer in from UBA', j.zenith_amount, 0, 'NGN', j.zenith_amount, 0, 1.0,
       'BANKING', 'BANK_TRANSFER', '00000000-0000-0000-0000-000000000000'::uuid
FROM _u2z j;

-- Posted ledger lines: Finance Cost (NIP fee)
INSERT INTO gl.posted_ledger_line (
    ledger_line_id, posting_year, organization_id,
    journal_entry_id, journal_line_id, posting_batch_id, fiscal_period_id,
    account_id, account_code, entry_date, posting_date,
    description, debit_amount, credit_amount,
    original_currency_code, original_debit_amount, original_credit_amount,
    exchange_rate, source_module, source_document_type, posted_by_user_id
)
SELECT j.pll_fee_id, j.posting_year, j.organization_id,
       j.journal_entry_id, j.je_fee_line_id, j.batch_id, j.fiscal_period_id,
       'e3b904ab-57bd-4429-95a8-a1438ae4ecca'::uuid, '6080',
       j.transaction_date, j.transaction_date,
       'NIP fee (UBA → ' || j.to_acct || ')', j.nip_fee, 0, 'NGN', j.nip_fee, 0, 1.0,
       'BANKING', 'BANK_TRANSFER', '00000000-0000-0000-0000-000000000000'::uuid
FROM _u2z j
WHERE j.nip_fee > 0;

-- Posted ledger lines: UBA credit
INSERT INTO gl.posted_ledger_line (
    ledger_line_id, posting_year, organization_id,
    journal_entry_id, journal_line_id, posting_batch_id, fiscal_period_id,
    account_id, account_code, entry_date, posting_date,
    description, debit_amount, credit_amount,
    original_currency_code, original_debit_amount, original_credit_amount,
    exchange_rate, source_module, source_document_type, posted_by_user_id
)
SELECT j.pll_credit_id, j.posting_year, j.organization_id,
       j.journal_entry_id, j.je_credit_line_id, j.batch_id, j.fiscal_period_id,
       j.uba_gl_id, j.uba_account_code,
       j.transaction_date, j.transaction_date,
       'Transfer out to ' || j.to_acct, 0, j.uba_amount, 'NGN', 0, j.uba_amount, 1.0,
       'BANKING', 'BANK_TRANSFER', '00000000-0000-0000-0000-000000000000'::uuid
FROM _u2z j;

-- Match: UBA debit line → credit journal line (Cr UBA)
INSERT INTO banking.bank_statement_line_matches (
    match_id, statement_line_id, journal_line_id, match_score, matched_at, is_primary
)
SELECT j.match_uba_id, j.uba_line_id, j.je_credit_line_id, 100, NOW(), true
FROM _u2z j;

-- Match: Zenith credit line → debit journal line (Dr Zenith)
INSERT INTO banking.bank_statement_line_matches (
    match_id, statement_line_id, journal_line_id, match_score, matched_at, is_primary
)
SELECT j.match_zenith_id, j.zenith_line_id, j.je_debit_line_id, 100, NOW(), true
FROM _u2z j;

-- Mark matched
UPDATE banking.bank_statement_lines sl
SET is_matched = true, matched_journal_line_id = j.je_credit_line_id
FROM _u2z j WHERE sl.line_id = j.uba_line_id;

UPDATE banking.bank_statement_lines sl
SET is_matched = true, matched_journal_line_id = j.je_debit_line_id
FROM _u2z j WHERE sl.line_id = j.zenith_line_id;

-- =========================================================================
-- PART D: Create GL journals for Zenith → UBA (same pattern, swap directions)
-- =========================================================================

CREATE TEMP TABLE _z2u AS
SELECT t.*,
       fp.fiscal_period_id,
       EXTRACT(YEAR FROM t.transaction_date)::int AS posting_year,
       src.account_code AS zenith_account_code,
       dst.account_code AS uba_account_code
FROM _zenith_to_uba t
JOIN gl.fiscal_period fp ON t.transaction_date BETWEEN fp.start_date AND fp.end_date
    AND fp.organization_id = t.organization_id
JOIN gl.account src ON src.account_id = t.zenith_gl_id
JOIN gl.account dst ON dst.account_id = t.uba_gl_id;

-- Journal entries
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
    j.journal_entry_id, j.organization_id,
    'BF-ZBU-' || LPAD(j.row_seq::text, 6, '0'),
    'STANDARD',
    j.transaction_date, j.transaction_date, j.fiscal_period_id,
    j.from_acct || ' → UBA transfer (NIP fee: ' || j.nip_fee || ')',
    'NGN', 1.0,
    j.zenith_amount, j.zenith_amount, j.zenith_amount, j.zenith_amount,
    'POSTED', false, false,
    'BANKING', 'BANK_TRANSFER',
    '00000000-0000-0000-0000-000000000000'::uuid, '00000000-0000-0000-0000-000000000000'::uuid, NOW(),
    '00000000-0000-0000-0000-000000000000'::uuid, NOW(),
    '00000000-0000-0000-0000-000000000000'::uuid, NOW(),
    j.batch_id, 1
FROM _z2u j;

-- Line 1: Dr UBA GL (principal received)
INSERT INTO gl.journal_entry_line (
    line_id, journal_entry_id, line_number, account_id, description,
    debit_amount, credit_amount, debit_amount_functional, credit_amount_functional,
    currency_code, exchange_rate
)
SELECT j.je_debit_line_id, j.journal_entry_id, 1, j.uba_gl_id,
       'Transfer in from ' || j.from_acct,
       j.uba_amount, 0, j.uba_amount, 0, 'NGN', 1.0
FROM _z2u j;

-- Line 2: Dr Finance Cost (NIP fee)
INSERT INTO gl.journal_entry_line (
    line_id, journal_entry_id, line_number, account_id, description,
    debit_amount, credit_amount, debit_amount_functional, credit_amount_functional,
    currency_code, exchange_rate
)
SELECT j.je_fee_line_id, j.journal_entry_id, 2,
       'e3b904ab-57bd-4429-95a8-a1438ae4ecca'::uuid,
       'NIP transfer fee (' || j.from_acct || ' → UBA)',
       j.nip_fee, 0, j.nip_fee, 0, 'NGN', 1.0
FROM _z2u j
WHERE j.nip_fee > 0;

-- Line 3: Cr Zenith GL (total debited)
INSERT INTO gl.journal_entry_line (
    line_id, journal_entry_id, line_number, account_id, description,
    debit_amount, credit_amount, debit_amount_functional, credit_amount_functional,
    currency_code, exchange_rate
)
SELECT j.je_credit_line_id, j.journal_entry_id, 3, j.zenith_gl_id,
       'Transfer out to UBA',
       0, j.zenith_amount, 0, j.zenith_amount, 'NGN', 1.0
FROM _z2u j;

-- Posting batches
INSERT INTO gl.posting_batch (
    batch_id, organization_id, fiscal_period_id, idempotency_key,
    source_module, batch_description,
    total_entries, posted_entries, failed_entries,
    status, submitted_by_user_id, submitted_at,
    processing_started_at, completed_at
)
SELECT j.batch_id, j.organization_id, j.fiscal_period_id,
       'zbu-' || j.zenith_line_id::text, 'BANKING',
       'Backfill: ' || j.from_acct || ' → UBA transfer',
       CASE WHEN j.nip_fee > 0 THEN 3 ELSE 2 END,
       CASE WHEN j.nip_fee > 0 THEN 3 ELSE 2 END, 0,
       'POSTED', '00000000-0000-0000-0000-000000000000'::uuid, NOW(), NOW(), NOW()
FROM _z2u j;

-- Posted ledger lines: UBA debit
INSERT INTO gl.posted_ledger_line (
    ledger_line_id, posting_year, organization_id,
    journal_entry_id, journal_line_id, posting_batch_id, fiscal_period_id,
    account_id, account_code, entry_date, posting_date,
    description, debit_amount, credit_amount,
    original_currency_code, original_debit_amount, original_credit_amount,
    exchange_rate, source_module, source_document_type, posted_by_user_id
)
SELECT j.pll_debit_id, j.posting_year, j.organization_id,
       j.journal_entry_id, j.je_debit_line_id, j.batch_id, j.fiscal_period_id,
       j.uba_gl_id, j.uba_account_code,
       j.transaction_date, j.transaction_date,
       'Transfer in from ' || j.from_acct, j.uba_amount, 0, 'NGN', j.uba_amount, 0, 1.0,
       'BANKING', 'BANK_TRANSFER', '00000000-0000-0000-0000-000000000000'::uuid
FROM _z2u j;

-- Posted ledger lines: Finance Cost
INSERT INTO gl.posted_ledger_line (
    ledger_line_id, posting_year, organization_id,
    journal_entry_id, journal_line_id, posting_batch_id, fiscal_period_id,
    account_id, account_code, entry_date, posting_date,
    description, debit_amount, credit_amount,
    original_currency_code, original_debit_amount, original_credit_amount,
    exchange_rate, source_module, source_document_type, posted_by_user_id
)
SELECT j.pll_fee_id, j.posting_year, j.organization_id,
       j.journal_entry_id, j.je_fee_line_id, j.batch_id, j.fiscal_period_id,
       'e3b904ab-57bd-4429-95a8-a1438ae4ecca'::uuid, '6080',
       j.transaction_date, j.transaction_date,
       'NIP fee (' || j.from_acct || ' → UBA)', j.nip_fee, 0, 'NGN', j.nip_fee, 0, 1.0,
       'BANKING', 'BANK_TRANSFER', '00000000-0000-0000-0000-000000000000'::uuid
FROM _z2u j
WHERE j.nip_fee > 0;

-- Posted ledger lines: Zenith credit
INSERT INTO gl.posted_ledger_line (
    ledger_line_id, posting_year, organization_id,
    journal_entry_id, journal_line_id, posting_batch_id, fiscal_period_id,
    account_id, account_code, entry_date, posting_date,
    description, debit_amount, credit_amount,
    original_currency_code, original_debit_amount, original_credit_amount,
    exchange_rate, source_module, source_document_type, posted_by_user_id
)
SELECT j.pll_credit_id, j.posting_year, j.organization_id,
       j.journal_entry_id, j.je_credit_line_id, j.batch_id, j.fiscal_period_id,
       j.zenith_gl_id, j.zenith_account_code,
       j.transaction_date, j.transaction_date,
       'Transfer out to UBA', 0, j.zenith_amount, 'NGN', 0, j.zenith_amount, 1.0,
       'BANKING', 'BANK_TRANSFER', '00000000-0000-0000-0000-000000000000'::uuid
FROM _z2u j;

-- Match: Zenith debit line → credit journal line (Cr Zenith)
INSERT INTO banking.bank_statement_line_matches (
    match_id, statement_line_id, journal_line_id, match_score, matched_at, is_primary
)
SELECT j.match_zenith_id, j.zenith_line_id, j.je_credit_line_id, 100, NOW(), true
FROM _z2u j;

-- Match: UBA credit line → debit journal line (Dr UBA)
INSERT INTO banking.bank_statement_line_matches (
    match_id, statement_line_id, journal_line_id, match_score, matched_at, is_primary
)
SELECT j.match_uba_id, j.uba_line_id, j.je_debit_line_id, 100, NOW(), true
FROM _z2u j;

-- Mark matched
UPDATE banking.bank_statement_lines sl
SET is_matched = true, matched_journal_line_id = j.je_credit_line_id
FROM _z2u j WHERE sl.line_id = j.zenith_line_id;

UPDATE banking.bank_statement_lines sl
SET is_matched = true, matched_journal_line_id = j.je_debit_line_id
FROM _z2u j WHERE sl.line_id = j.uba_line_id;

-- =========================================================================
-- Fix statement counters
-- =========================================================================
UPDATE banking.bank_statements s
SET matched_lines = sub.matched, unmatched_lines = sub.unmatched
FROM (
    SELECT sl.statement_id,
           count(*) FILTER (WHERE sl.is_matched = true) AS matched,
           count(*) FILTER (WHERE sl.is_matched = false) AS unmatched
    FROM banking.bank_statement_lines sl GROUP BY sl.statement_id
) sub
WHERE s.statement_id = sub.statement_id
  AND (s.matched_lines <> sub.matched OR s.unmatched_lines <> sub.unmatched);

-- =========================================================================
-- Verification
-- =========================================================================
SELECT 'UBA → Zenith' AS flow, count(*) AS matched,
       SUM(nip_fee) AS total_nip_fees FROM _u2z
UNION ALL
SELECT 'Zenith → UBA', count(*), SUM(nip_fee) FROM _z2u;

SELECT ba.account_name,
       s.matched_lines, s.unmatched_lines,
       ROUND(100.0 * s.matched_lines / NULLIF(s.matched_lines + s.unmatched_lines, 0), 1) AS match_pct
FROM banking.bank_statements s
JOIN banking.bank_accounts ba ON s.bank_account_id = ba.bank_account_id
WHERE ba.account_name IN ('UBA 96 (Main)', 'Zenith 461', 'Zenith 523', 'Zenith 454 (Int Project)')
ORDER BY ba.account_name, s.period_start;

DROP TABLE _uba_to_zenith;
DROP TABLE _zenith_to_uba;
DROP TABLE _u2z;
DROP TABLE _z2u;

COMMIT;
