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
-- reversal, if it was itself reversed, if it never reached the ledger, or if
-- its accounts, amounts, currency, period or dimensions differ.
--
-- This detector therefore:
--
-- * classifies the two idempotency namespaces by their COMPLETE keys;
-- * retains only currently effective immutable-ledger effects;
-- * requires exactly one line-keyed canonical effect per affected line;
-- * compares every journal-keyed target with that canonical effect exactly;
-- * emits a one-row-per-target schedule and a digest Finance can approve; and
-- * fails closed rather than calling a merely associated row a duplicate.
-- ===========================================================================

\set ON_ERROR_STOP on
\timing on
\pset pager off
\set ORG '00000000-0000-0000-0000-000000000001'

BEGIN;

CREATE TEMP TABLE fee AS
WITH parsed AS (
  SELECT je.journal_entry_id, je.organization_id, je.journal_number,
         je.status, je.is_reversal, je.reversal_journal_id,
         je.entry_date, je.posting_date, je.fiscal_period_id,
         je.currency_code, je.exchange_rate,
         je.total_debit, je.total_credit,
         je.total_debit_functional, je.total_credit_functional,
         je.posting_batch_id,
         CASE
           WHEN je.correlation_id ~ '^bank-fee-[0-9a-fA-F-]{36}$'
             THEN substring(je.correlation_id from 10)::uuid
         END AS line_id,
         b.idempotency_key,
         (SELECT count(*) FROM gl.posted_ledger_line p
           WHERE p.organization_id = je.organization_id
             AND p.journal_entry_id = je.journal_entry_id) AS ledger_rows
  FROM gl.journal_entry je
  LEFT JOIN gl.posting_batch b
    ON b.batch_id = je.posting_batch_id
   AND b.organization_id = je.organization_id
  WHERE je.organization_id = :'ORG'::uuid
    AND je.source_document_type = 'BANK_FEE'
)
SELECT p.*,
       CASE
         WHEN p.is_reversal
           THEN 'REVERSAL'
         WHEN p.line_id IS NOT NULL
          AND p.idempotency_key = concat(
                p.organization_id::text, ':BANKING:', p.line_id::text,
                ':bank-fee:v1')
           THEN 'LINE_KEYED'
         WHEN p.idempotency_key = concat(
                'backfill-stranded-bank-fees-', p.journal_number)
           THEN 'JOURNAL_KEYED'
         ELSE 'UNKNOWN'
       END AS key_namespace
FROM parsed p;
CREATE INDEX ON fee (line_id);
CREATE UNIQUE INDEX ON fee (journal_entry_id);

-- The complete immutable effect. Journal ids, line ids and batch ids are
-- deliberately absent: those identify the copy, not the accounting effect.
CREATE TEMP TABLE ledger_effect AS
SELECT p.journal_entry_id,
       jsonb_agg(
         jsonb_build_array(
           p.account_id, p.account_code,
           p.debit_amount, p.credit_amount,
           p.original_currency_code,
           p.original_debit_amount, p.original_credit_amount, p.exchange_rate,
           p.business_unit_id, p.cost_center_id, p.project_id, p.segment_id,
           p.entry_date, p.posting_date, p.fiscal_period_id,
           p.source_module, p.source_document_type, p.source_document_id
         )
         ORDER BY p.account_id, p.debit_amount, p.credit_amount,
                  p.original_currency_code, p.original_debit_amount,
                  p.original_credit_amount, p.exchange_rate,
                  p.business_unit_id, p.cost_center_id, p.project_id,
                  p.segment_id, p.entry_date, p.posting_date,
                  p.fiscal_period_id, p.source_module,
                  p.source_document_type, p.source_document_id
       ) AS ledger_signature
FROM gl.posted_ledger_line p
JOIN fee f ON f.journal_entry_id = p.journal_entry_id
          AND f.organization_id = p.organization_id
GROUP BY p.journal_entry_id;
CREATE UNIQUE INDEX ON ledger_effect (journal_entry_id);

CREATE TEMP TABLE live_fee AS
SELECT f.*,
       jsonb_build_object(
         'entry_date', f.entry_date,
         'posting_date', f.posting_date,
         'fiscal_period_id', f.fiscal_period_id,
         'currency_code', f.currency_code,
         'exchange_rate', f.exchange_rate,
         'total_debit', f.total_debit,
         'total_credit', f.total_credit,
         'total_debit_functional', f.total_debit_functional,
         'total_credit_functional', f.total_credit_functional,
         'ledger', e.ledger_signature
       ) AS full_effect_signature
FROM fee f
JOIN ledger_effect e ON e.journal_entry_id = f.journal_entry_id
WHERE f.status = 'POSTED'
  AND NOT f.is_reversal
  AND f.reversal_journal_id IS NULL
  AND f.ledger_rows > 0;
CREATE INDEX ON live_fee (line_id);
CREATE UNIQUE INDEX ON live_fee (journal_entry_id);

\echo ''
\echo '===== D0. FAIL-CLOSED namespace classification ====='
CREATE TEMP TABLE namespace_check AS
SELECT count(*) FILTER (WHERE status = 'POSTED') AS posted_journals,
       count(*) FILTER (WHERE status = 'POSTED' AND key_namespace = 'LINE_KEYED')
         AS line_keyed,
       count(*) FILTER (WHERE status = 'POSTED' AND key_namespace = 'JOURNAL_KEYED')
         AS journal_keyed,
       count(*) FILTER (WHERE status = 'POSTED' AND key_namespace = 'REVERSAL')
         AS reversal_journals,
       count(*) FILTER (WHERE status = 'POSTED' AND key_namespace = 'UNKNOWN')
         AS unknown_keys
FROM fee;
SELECT * FROM namespace_check;

DO $namespace$
DECLARE n integer;
BEGIN
  SELECT unknown_keys INTO n FROM namespace_check;
  IF n <> 0 THEN
    RAISE EXCEPTION
      'bank-fee namespace classification failed closed: % POSTED journals have an unknown complete idempotency key', n;
  END IF;
END
$namespace$;

CREATE TEMP TABLE affected_line AS
SELECT line_id,
       count(*) FILTER (WHERE key_namespace = 'LINE_KEYED') AS canonical_live,
       count(*) FILTER (WHERE key_namespace = 'JOURNAL_KEYED') AS bypassed_live,
       count(*) FILTER (WHERE key_namespace = 'UNKNOWN') AS unknown_live
FROM live_fee
GROUP BY line_id
HAVING count(*) FILTER (WHERE key_namespace = 'JOURNAL_KEYED') > 0;
CREATE UNIQUE INDEX ON affected_line (line_id);

DO $cardinality$
DECLARE n integer;
BEGIN
  SELECT count(*) INTO n
  FROM affected_line
  WHERE canonical_live <> 1 OR bypassed_live < 1 OR unknown_live <> 0;
  IF n <> 0 THEN
    RAISE EXCEPTION
      'bank-fee canonical cardinality failed closed on % affected statement lines; expected exactly one live line-keyed canonical and at least one live journal-keyed target', n;
  END IF;
END
$cardinality$;

-- This is the approval unit: one target journal, its one canonical journal,
-- the exact immutable effect comparison, and a row hash. Nothing downstream
-- reconstructs the population from a count or a heuristic key.
CREATE TEMP TABLE reversal_schedule AS
SELECT target.line_id,
       target.journal_entry_id AS target_journal_entry_id,
       target.journal_number AS target_journal_number,
       target.posting_batch_id AS target_posting_batch_id,
       target.idempotency_key AS target_idempotency_key,
       canonical.journal_entry_id AS canonical_journal_entry_id,
       canonical.journal_number AS canonical_journal_number,
       canonical.posting_batch_id AS canonical_posting_batch_id,
       target.posting_date,
       target.fiscal_period_id,
       target.currency_code,
       target.total_debit_functional,
       target.full_effect_signature = canonical.full_effect_signature
         AS exact_effect_match,
       md5(target.full_effect_signature::text) AS target_effect_hash,
       md5(canonical.full_effect_signature::text) AS canonical_effect_hash,
       md5(concat_ws(
         '|', target.journal_entry_id::text, target.posting_batch_id::text,
         target.idempotency_key, canonical.journal_entry_id::text,
         md5(target.full_effect_signature::text)
       )) AS schedule_row_hash,
       EXISTS (
         SELECT 1
         FROM banking.bank_statement_lines sl
         JOIN banking.bank_statements bs
           ON bs.statement_id = sl.statement_id
          AND bs.organization_id = target.organization_id
         WHERE sl.line_id = target.line_id
       ) AS statement_line_resolves
FROM live_fee target
JOIN live_fee canonical
  ON canonical.line_id = target.line_id
 AND canonical.key_namespace = 'LINE_KEYED'
WHERE target.key_namespace = 'JOURNAL_KEYED';
CREATE UNIQUE INDEX ON reversal_schedule (target_journal_entry_id);
CREATE UNIQUE INDEX ON reversal_schedule (target_posting_batch_id);
CREATE UNIQUE INDEX ON reversal_schedule (target_idempotency_key);

DO $schedule$
DECLARE expected integer;
DECLARE actual integer;
DECLARE mismatched integer;
BEGIN
  SELECT COALESCE(sum(bypassed_live), 0) INTO expected FROM affected_line;
  SELECT count(*), count(*) FILTER (WHERE NOT exact_effect_match)
    INTO actual, mismatched
  FROM reversal_schedule;

  IF actual <> expected THEN
    RAISE EXCEPTION
      'bank-fee reversal schedule is incomplete: expected % live journal-keyed targets, materialized %', expected, actual;
  END IF;
  IF mismatched <> 0 THEN
    RAISE EXCEPTION
      'bank-fee reversal schedule refused: % journal-keyed targets do not exactly match their canonical immutable effect', mismatched;
  END IF;
END
$schedule$;

-- Sensitivity proof for the equality predicate. The fixture has the same shape
-- as `full_effect_signature`; changing currency, period, account, amount or a
-- dimension must make it unequal. This prevents a vacuous exact-match claim.
CREATE TEMP TABLE effect_comparator_canary AS
WITH fixture AS (
  SELECT jsonb_build_object(
    'entry_date', DATE '2026-01-01',
    'posting_date', DATE '2026-01-01',
    'fiscal_period_id', '00000000-0000-0000-0000-000000000011'::uuid,
    'currency_code', 'NGN',
    'exchange_rate', 1.0000000000,
    'total_debit', 1.000000,
    'total_credit', 1.000000,
    'total_debit_functional', 1.000000,
    'total_credit_functional', 1.000000,
    'ledger', jsonb_build_array(jsonb_build_array(
      '00000000-0000-0000-0000-000000000021'::uuid, '1000',
      1.000000, 0.000000, 'NGN', 1.000000, 0.000000, 1.0000000000,
      NULL, '00000000-0000-0000-0000-000000000031'::uuid,
      NULL, NULL, DATE '2026-01-01', DATE '2026-01-01',
      '00000000-0000-0000-0000-000000000011'::uuid,
      'BANKING', 'BANK_FEE', NULL
    ))
  ) AS signature
), probes AS (
  SELECT 'unchanged control' AS probe, signature AS candidate, true AS expected
  FROM fixture
  UNION ALL
  SELECT 'currency', jsonb_set(signature, '{currency_code}', '"USD"'), false
  FROM fixture
  UNION ALL
  SELECT 'period', jsonb_set(
    signature, '{fiscal_period_id}',
    '"00000000-0000-0000-0000-000000000012"'), false
  FROM fixture
  UNION ALL
  SELECT 'account', jsonb_set(
    signature, '{ledger,0,0}',
    '"00000000-0000-0000-0000-000000000022"'), false
  FROM fixture
  UNION ALL
  SELECT 'amount', jsonb_set(signature, '{ledger,0,2}', '1.000001'), false
  FROM fixture
  UNION ALL
  SELECT 'dimension', jsonb_set(
    signature, '{ledger,0,9}',
    '"00000000-0000-0000-0000-000000000032"'), false
  FROM fixture
)
SELECT p.probe, (p.candidate = f.signature) AS observed, p.expected
FROM probes p CROSS JOIN fixture f;

DO $sensitivity$
DECLARE n integer;
BEGIN
  SELECT count(*) INTO n
  FROM effect_comparator_canary
  WHERE observed IS DISTINCT FROM expected;
  IF n <> 0 THEN
    RAISE EXCEPTION
      'exact-effect sensitivity proof failed: % comparator probes returned the wrong result', n;
  END IF;
  RAISE NOTICE
    'exact-effect sensitivity proof PASSED: currency, period, account, amount and dimension changes are rejected';
END
$sensitivity$;

\echo ''
\echo '===== D1. POSTED bank fees by COMPLETE IDEMPOTENCY NAMESPACE ====='
SELECT key_namespace AS namespace,
       count(*) AS posted_journals,
       count(DISTINCT line_id) AS statement_lines,
       sum(total_debit_functional) AS gross,
       count(*) FILTER (WHERE ledger_rows > 0) AS with_ledger_rows,
       count(*) FILTER (WHERE is_reversal OR reversal_journal_id IS NOT NULL)
         AS reversed_or_reversal
FROM fee WHERE status = 'POSTED' GROUP BY 1 ORDER BY 1;

\echo ''
\echo '===== D2. CURRENT effects per affected line, by namespace ====='
SELECT canonical_live, bypassed_live, count(*) AS statement_lines
FROM affected_line GROUP BY 1,2 ORDER BY 3 DESC;

\echo ''
\echo '===== D3. PROVEN duplicate CURRENT effect ====='
SELECT count(DISTINCT line_id) AS lines_with_duplicate_effect,
       count(*) AS duplicate_postings,
       sum(total_debit_functional) AS duplicate_effect_amount,
       count(*) FILTER (WHERE NOT statement_line_resolves)
         AS postings_on_lines_that_do_not_resolve,
       sum(total_debit_functional) FILTER (WHERE statement_line_resolves)
         AS amount_on_resolvable_lines,
       count(*) FILTER (WHERE exact_effect_match) AS exact_effect_matches
FROM reversal_schedule;

\echo ''
\echo '===== D4. Every target is unique and exactly matches its canonical ====='
SELECT count(*) AS reversal_targets,
       count(DISTINCT target_journal_entry_id) AS distinct_target_journals,
       count(DISTINCT target_posting_batch_id) AS distinct_target_batches,
       count(DISTINCT target_idempotency_key) AS distinct_target_keys,
       count(*) FILTER (WHERE exact_effect_match) AS exact_effect_matches,
       count(*) FILTER (WHERE target_effect_hash = canonical_effect_hash)
         AS equal_effect_hashes
FROM reversal_schedule;

\echo ''
\echo '===== D5. The canonical posting retained per affected line ====='
SELECT DISTINCT line_id, canonical_journal_entry_id, canonical_journal_number,
       canonical_posting_batch_id
FROM reversal_schedule
ORDER BY line_id;

\echo ''
\echo '===== D6. Finance approval binding — count, amount and schedule digest ====='
SELECT count(*) AS reversal_targets,
       sum(total_debit_functional) AS duplicate_effect_amount,
       md5(COALESCE(string_agg(
         schedule_row_hash, ',' ORDER BY target_journal_entry_id), ''))
         AS reversal_schedule_digest,
       min(posting_date) AS first_posting_date,
       max(posting_date) AS last_posting_date
FROM reversal_schedule;

\echo ''
\echo '===== D6a. Every target, named — keep this output inside the Finance boundary ====='
SELECT target_journal_entry_id, target_journal_number,
       target_posting_batch_id, target_idempotency_key,
       line_id, canonical_journal_entry_id, canonical_journal_number,
       posting_date, fiscal_period_id, currency_code,
       total_debit_functional, target_effect_hash, schedule_row_hash,
       statement_line_resolves
FROM reversal_schedule
ORDER BY total_debit_functional DESC, target_journal_number;

\echo ''
\echo '===== D6b. Aggregate correcting-reversal effect by account ====='
\echo '-- A correction reverses each target: target credits become correction debits'
\echo '-- and target debits become correction credits.'
SELECT p.account_code,
       sum(p.credit_amount) AS correcting_debit,
       sum(p.debit_amount) AS correcting_credit
FROM reversal_schedule s
JOIN gl.posted_ledger_line p
  ON p.journal_entry_id = s.target_journal_entry_id
 AND p.organization_id = :'ORG'::uuid
GROUP BY p.account_code
ORDER BY p.account_code;

\echo ''
\echo '===== D7. Do any APPROVED journals sit on the affected lines? ====='
\echo '-- If zero, the Gate G backlog and this Gate D defect are disjoint'
\echo '-- populations and can be dispositioned independently.'
SELECT count(*) AS approved_on_affected_lines
FROM fee f
WHERE f.status = 'APPROVED'
  AND f.line_id IN (SELECT line_id FROM affected_line);

ROLLBACK;
