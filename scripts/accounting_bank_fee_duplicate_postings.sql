-- ===========================================================================
-- Bank-fee duplicate POSTED effects — a GATE D question, not a Gate G one.
--
-- Read-only: one transaction, temp tables, ending in ROLLBACK. Run against an
-- ISOLATED RESTORED DATABASE.
--
-- These journals are already in the posted ledger, inside the Gate D backfill
-- population. They are not backlog. If confirmed they need Finance-approved
-- one-to-one correcting reversals before cutover.
--
-- WHAT IT ESTABLISHES, AND WHY THE FIRST ATTEMPT DID NOT
--
-- An earlier calculation was `(posted row count - 1) x source fee`, grouped by
-- a heuristic bucket. That proves excess ROWS under a bucket. It does not prove
-- excess ECONOMIC EFFECT: a second posted row is not a duplicate if it is a
-- reversal, if it was itself reversed, or if it never reached the ledger.
--
-- So each posted row is tested for CURRENT EFFECTIVENESS — not a reversal, not
-- since reversed, and carrying rows in `gl.posted_ledger_line` — and the
-- canonical posting is identified from its idempotency key rather than assumed
-- to be "the first one".
-- ===========================================================================

\set ON_ERROR_STOP on
\timing on
\pset pager off
\set ORG '00000000-0000-0000-0000-000000000001'

BEGIN;

CREATE TEMP TABLE fee AS
SELECT je.journal_entry_id, je.journal_number, je.status, je.is_reversal,
       je.reversal_journal_id, je.total_debit_functional, je.posting_batch_id,
       substring(je.correlation_id from 10)::uuid AS line_id,
       b.idempotency_key,
       (split_part(b.idempotency_key, ':', 3) = substring(je.correlation_id from 10))
                                                                    AS line_keyed,
       (SELECT count(*) FROM gl.posted_ledger_line p
         WHERE p.journal_entry_id = je.journal_entry_id)             AS ledger_rows
FROM gl.journal_entry je
LEFT JOIN gl.posting_batch b ON b.batch_id = je.posting_batch_id
WHERE je.organization_id = :'ORG'::uuid
  AND je.source_document_type = 'BANK_FEE'
  AND je.correlation_id ~ '^bank-fee-[0-9a-fA-F-]{36}$';
CREATE INDEX ON fee (line_id);

\echo ''
\echo '===== D1. POSTED bank fees by IDEMPOTENCY NAMESPACE ====='
\echo '-- This is the whole finding. The ledger boundary is keyed on the statement'
\echo '-- line. A second namespace keyed on the JOURNAL bypasses it entirely.'
SELECT CASE WHEN line_keyed THEN 'line-keyed: <org>:BANKING:<line_id>:bank-fee:v1'
            ELSE 'journal-keyed: backfill-stranded-bank-fees-<journal_number>' END AS namespace,
       count(*) AS posted_journals,
       count(DISTINCT line_id) AS statement_lines,
       sum(total_debit_functional) AS gross,
       count(*) FILTER (WHERE ledger_rows > 0) AS with_ledger_rows,
       count(*) FILTER (WHERE is_reversal OR reversal_journal_id IS NOT NULL) AS reversed_or_reversal
FROM fee WHERE status = 'POSTED' GROUP BY 1;

\echo ''
\echo '===== D2. Postings per line, by namespace ====='
WITH per AS (
  SELECT line_id,
         count(*) FILTER (WHERE line_keyed)     AS canonical,
         count(*) FILTER (WHERE NOT line_keyed) AS bypassed
  FROM fee WHERE status='POSTED' GROUP BY 1)
SELECT canonical, bypassed, count(*) AS statement_lines
FROM per GROUP BY 1,2 ORDER BY 3 DESC;

\echo ''
\echo '===== D3. PROVEN duplicate CURRENT effect ====='
\echo '-- Only rows that are effective right now: not a reversal, not since'
\echo '-- reversed, and present in the posted ledger.'
WITH per AS (
  SELECT f.line_id,
         count(*) FILTER (WHERE NOT f.line_keyed AND f.ledger_rows > 0
                            AND NOT f.is_reversal AND f.reversal_journal_id IS NULL) AS live_bypassed,
         max(f.total_debit_functional) AS fee_amount,
         bool_or(sl.line_id IS NOT NULL) AS line_resolves
  FROM fee f
  LEFT JOIN banking.bank_statement_lines sl ON sl.line_id = f.line_id
  WHERE f.status='POSTED' GROUP BY 1)
SELECT count(*) FILTER (WHERE live_bypassed > 0)                    AS lines_with_duplicate_effect,
       sum(live_bypassed)                                           AS duplicate_postings,
       sum(live_bypassed * fee_amount)                              AS duplicate_effect_amount,
       count(*) FILTER (WHERE live_bypassed > 0 AND NOT line_resolves) AS lines_that_do_not_resolve,
       sum(live_bypassed * fee_amount) FILTER (WHERE line_resolves)  AS amount_on_resolvable_lines
FROM per;

\echo ''
\echo '===== D4. Every duplicate is individually identifiable ====='
\echo '-- One-to-one correcting reversals are possible because the bypassed'
\echo '-- postings carry a distinguishable key. Nothing has to be guessed.'
SELECT count(*) AS bypassed_postings,
       count(*) FILTER (WHERE idempotency_key LIKE 'backfill-stranded-bank-fees-%') AS carry_the_prefix,
       count(DISTINCT posting_batch_id) AS distinct_batches
FROM fee WHERE status='POSTED' AND NOT line_keyed;

\echo ''
\echo '===== D5. The canonical posting per affected line ====='
SELECT f.line_id, f.journal_number AS canonical_journal, f.total_debit_functional
FROM fee f
WHERE f.status='POSTED' AND f.line_keyed
  AND f.line_id IN (SELECT line_id FROM fee WHERE status='POSTED' AND NOT line_keyed)
ORDER BY f.total_debit_functional DESC;

\echo ''
\echo '===== D6. Every bypassed posting, named (for the reversal schedule) ====='
SELECT f.journal_number, f.total_debit_functional, f.idempotency_key
FROM fee f WHERE f.status='POSTED' AND NOT f.line_keyed
ORDER BY f.total_debit_functional DESC, f.journal_number;

\echo ''
\echo '===== D7. Do any APPROVED journals sit on the affected lines? ====='
\echo '-- If zero, the Gate G backlog and this Gate D defect are disjoint'
\echo '-- populations and can be dispositioned independently.'
SELECT count(*) AS approved_on_affected_lines
FROM fee f
WHERE f.status='APPROVED'
  AND f.line_id IN (SELECT line_id FROM fee WHERE status='POSTED' AND NOT line_keyed);

ROLLBACK;
