-- ===========================================================================
-- Bank-fee wrong-account correction schedule — Gate D evidence only.
--
-- Read-only: one REPEATABLE READ transaction, temp tables, ending in ROLLBACK.
-- Run only against an ISOLATED RESTORED DATABASE. This script does not create
-- a reversal, update a journal, or write an accounting row.
--
-- The exact-duplicate detector correctly refused these 429 journals: their
-- money and dimensions match the line-keyed canonical postings, but their bank
-- account does not. This purpose-built detector proves the narrower fact that
-- each journal-keyed effect differs by exactly one approved account mapping,
-- proves that ReversalService will read lines identical to the posted effect,
-- binds one target per linked reversal, and simulates the resulting net effect.
--
-- This is an approval schedule, not execution authority. A writer must consume
-- the exact schedule and digest under separate Finance approval, use the normal
-- linked-reversal service, and prove its postconditions in one transaction.
-- ===========================================================================

\set ON_ERROR_STOP on
\timing on
\pset pager off

\if :{?ORG}
\else
\set ORG '00000000-0000-0000-0000-000000000001'
\endif

\if :{?REVERSAL_DATE}
\else
\set REVERSAL_DATE '2026-08-23'
\endif

BEGIN ISOLATION LEVEL REPEATABLE READ;

-- The approved correction vocabulary is closed. A third substitution, a
-- change in direction, or a changed population is a refusal, not a new plan.
CREATE TEMP TABLE approved_account_mapping (
  mapping_name text PRIMARY KEY,
  canonical_bank_code text NOT NULL,
  legacy_bank_code text NOT NULL,
  expected_targets integer NOT NULL,
  expected_statement_line_resolves boolean NOT NULL
);
INSERT INTO approved_account_mapping VALUES
  ('PAYSTACK_OPEX_LEGACY_TO_1211', '1211', 'Paystack OPEX - DT', 352, true),
  ('ZENITH_USD_LEGACY_TO_1207', '1207', 'Zenith USD - DT', 77, false);

-- The retained line-keyed postings wrote their immutable ledger rows in one
-- later batch. The target headers have the same intended dates as the
-- canonicals, but the target ledger rows landed in their 2025 periods while
-- the canonical ledger rows landed in March 2026. That is part of the plan,
-- not a field the economic comparator may erase.
CREATE TEMP TABLE approved_canonical_timing (
  posting_date date PRIMARY KEY,
  fiscal_period_id uuid NOT NULL,
  period_name text NOT NULL
);
INSERT INTO approved_canonical_timing VALUES
  ('2026-03-13', '7bc1edbb-270c-4096-b9e4-67cc72dd44a4', 'March 2026');

CREATE TEMP TABLE correction_period AS
SELECT fiscal_period_id, period_name, start_date, end_date, status,
       is_adjustment_period, is_closing_period
FROM gl.fiscal_period
WHERE organization_id = :'ORG'::uuid
  AND :'REVERSAL_DATE'::date BETWEEN start_date AND end_date;

DO $period$
DECLARE n integer;
DECLARE bad integer;
BEGIN
  SELECT count(*), count(*) FILTER (
           WHERE status <> 'OPEN'
              OR is_adjustment_period
              OR is_closing_period
         )
    INTO n, bad
  FROM correction_period;
  IF n <> 1 OR bad <> 0 THEN
    RAISE EXCEPTION
      'correction date refused: expected exactly one ordinary OPEN fiscal period, found % (% inadmissible)',
      n, bad;
  END IF;
END
$period$;

CREATE TEMP TABLE fee AS
WITH parsed AS (
  SELECT je.journal_entry_id, je.organization_id, je.journal_number,
         je.status, je.is_reversal, je.reversal_journal_id,
         je.entry_date, je.posting_date, je.fiscal_period_id,
         je.currency_code, je.exchange_rate,
         je.total_debit, je.total_credit,
         je.total_debit_functional, je.total_credit_functional,
         je.posting_batch_id, je.source_module, je.source_document_type,
         je.source_document_id, je.correlation_id,
         CASE
           WHEN je.correlation_id ~ '^bank-fee-[0-9a-fA-F-]{36}$'
             THEN substring(je.correlation_id from 10)::uuid
         END AS statement_line_id,
         b.idempotency_key,
         (SELECT count(*)
            FROM gl.posted_ledger_line p
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
         WHEN p.is_reversal THEN 'REVERSAL'
         WHEN p.statement_line_id IS NOT NULL
          AND p.idempotency_key = concat(
                p.organization_id::text, ':BANKING:',
                p.statement_line_id::text, ':bank-fee:v1')
           THEN 'LINE_KEYED'
         WHEN p.idempotency_key = concat(
                'backfill-stranded-bank-fees-', p.journal_number)
           THEN 'JOURNAL_KEYED'
         ELSE 'UNKNOWN'
       END AS key_namespace
FROM parsed p;
CREATE UNIQUE INDEX ON fee (journal_entry_id);
CREATE INDEX ON fee (statement_line_id);

CREATE TEMP TABLE ledger_effect AS
SELECT p.journal_entry_id,
       count(*) AS ledger_rows,
       jsonb_agg(
         p.account_code ORDER BY p.account_code, p.account_id,
                                 p.debit_amount, p.credit_amount
       ) AS account_code_multiset,
       jsonb_agg(
         jsonb_build_array(
           p.debit_amount, p.credit_amount,
           p.original_currency_code,
           p.original_debit_amount, p.original_credit_amount, p.exchange_rate,
           p.business_unit_id, p.cost_center_id, p.project_id, p.segment_id,
           p.entry_date,
           p.source_module, p.source_document_type, p.source_document_id
         )
         ORDER BY p.debit_amount, p.credit_amount,
                  p.original_currency_code, p.original_debit_amount,
                  p.original_credit_amount, p.exchange_rate,
                  p.business_unit_id, p.cost_center_id, p.project_id,
                  p.segment_id, p.entry_date, p.source_module,
                  p.source_document_type, p.source_document_id
       ) AS non_account_signature,
       bool_and(p.entry_date = f.entry_date)
         AS ledger_entry_date_matches_header,
       bool_and(p.posting_date = f.posting_date)
         AS ledger_posting_date_matches_header,
       bool_and(p.fiscal_period_id = f.fiscal_period_id)
         AS ledger_period_matches_header,
       min(p.posting_date) AS first_ledger_posting_date,
       max(p.posting_date) AS last_ledger_posting_date,
       min(p.fiscal_period_id::text)::uuid AS first_ledger_period_id,
       max(p.fiscal_period_id::text)::uuid AS last_ledger_period_id,
       jsonb_agg(
         jsonb_build_array(
           p.journal_line_id, p.account_id, p.account_code,
           p.debit_amount, p.credit_amount,
           p.original_currency_code,
           p.original_debit_amount, p.original_credit_amount, p.exchange_rate,
           p.business_unit_id, p.cost_center_id, p.project_id, p.segment_id,
           p.entry_date, p.posting_date, p.fiscal_period_id,
           p.source_module, p.source_document_type, p.source_document_id
         )
         ORDER BY p.journal_line_id
       ) AS persisted_effect_signature
FROM gl.posted_ledger_line p
JOIN fee f
  ON f.journal_entry_id = p.journal_entry_id
 AND f.organization_id = p.organization_id
GROUP BY p.journal_entry_id;
CREATE UNIQUE INDEX ON ledger_effect (journal_entry_id);

-- Per-account signatures prove the common 6080 leg is unchanged and the
-- legacy bank leg has the exact effect of the canonical bank leg.
CREATE TEMP TABLE ledger_account_effect AS
SELECT p.journal_entry_id, p.account_code,
       jsonb_agg(
         jsonb_build_array(
           p.debit_amount, p.credit_amount,
           p.original_currency_code,
           p.original_debit_amount, p.original_credit_amount, p.exchange_rate,
           p.business_unit_id, p.cost_center_id, p.project_id, p.segment_id,
           p.entry_date,
           p.source_module, p.source_document_type, p.source_document_id
         )
         ORDER BY p.debit_amount, p.credit_amount,
                  p.original_currency_code, p.original_debit_amount,
                  p.original_credit_amount, p.exchange_rate,
                  p.business_unit_id, p.cost_center_id, p.project_id,
                  p.segment_id, p.entry_date, p.source_module,
                  p.source_document_type, p.source_document_id
       ) AS effect_signature
FROM gl.posted_ledger_line p
JOIN fee f
  ON f.journal_entry_id = p.journal_entry_id
 AND f.organization_id = p.organization_id
GROUP BY p.journal_entry_id, p.account_code;
CREATE UNIQUE INDEX ON ledger_account_effect (journal_entry_id, account_code);

CREATE TEMP TABLE live_fee AS
SELECT f.*,
       e.account_code_multiset,
       e.non_account_signature,
       e.ledger_entry_date_matches_header,
       e.ledger_posting_date_matches_header,
       e.ledger_period_matches_header,
       e.first_ledger_posting_date,
       e.last_ledger_posting_date,
       e.first_ledger_period_id,
       e.last_ledger_period_id,
       e.persisted_effect_signature,
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
         'source_module', f.source_module,
         'source_document_type', f.source_document_type,
         'source_document_id', f.source_document_id,
         'correlation_id', f.correlation_id
       ) AS header_signature
FROM fee f
JOIN ledger_effect e ON e.journal_entry_id = f.journal_entry_id
WHERE f.status = 'POSTED'
  AND NOT f.is_reversal
  AND f.reversal_journal_id IS NULL
  AND f.ledger_rows > 0;
CREATE UNIQUE INDEX ON live_fee (journal_entry_id);
CREATE INDEX ON live_fee (statement_line_id);

\echo ''
\echo '===== W0. FAIL-CLOSED namespace and cardinality ====='
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
SELECT statement_line_id,
       count(*) FILTER (WHERE key_namespace = 'LINE_KEYED') AS canonical_live,
       count(*) FILTER (WHERE key_namespace = 'JOURNAL_KEYED') AS target_live,
       count(*) FILTER (WHERE key_namespace = 'UNKNOWN') AS unknown_live
FROM live_fee
GROUP BY statement_line_id
HAVING count(*) FILTER (WHERE key_namespace = 'JOURNAL_KEYED') > 0;
CREATE UNIQUE INDEX ON affected_line (statement_line_id);

DO $cardinality$
DECLARE bad integer;
BEGIN
  SELECT count(*) INTO bad
  FROM affected_line
  WHERE statement_line_id IS NULL
     OR canonical_live <> 1
     OR target_live < 1
     OR unknown_live <> 0;
  IF bad <> 0 THEN
    RAISE EXCEPTION
      'wrong-account correction cardinality failed closed on % affected statement lines', bad;
  END IF;
END
$cardinality$;

-- ReversalService reconstructs the reversal from journal_entry_line. Prove
-- those mutable source rows still reproduce every immutable target ledger row
-- exactly before describing a linked reversal as safe.
CREATE TEMP TABLE target_journal_parity AS
SELECT target.journal_entry_id,
       (SELECT count(*)
          FROM gl.journal_entry_line jl
         WHERE jl.journal_entry_id = target.journal_entry_id) AS journal_rows,
       (SELECT count(*)
          FROM gl.posted_ledger_line p
         WHERE p.organization_id = target.organization_id
           AND p.journal_entry_id = target.journal_entry_id) AS ledger_rows,
       (SELECT count(*)
          FROM gl.posted_ledger_line p
          LEFT JOIN gl.journal_entry_line jl
            ON jl.journal_entry_id = p.journal_entry_id
           AND jl.line_id = p.journal_line_id
         WHERE p.organization_id = target.organization_id
           AND p.journal_entry_id = target.journal_entry_id
           AND (
             jl.line_id IS NULL
             OR jl.account_id IS DISTINCT FROM p.account_id
             OR jl.debit_amount_functional IS DISTINCT FROM p.debit_amount
             OR jl.credit_amount_functional IS DISTINCT FROM p.credit_amount
             OR jl.debit_amount IS DISTINCT FROM p.original_debit_amount
             OR jl.credit_amount IS DISTINCT FROM p.original_credit_amount
             OR jl.currency_code IS DISTINCT FROM p.original_currency_code
             OR jl.exchange_rate IS DISTINCT FROM p.exchange_rate
             OR jl.business_unit_id IS DISTINCT FROM p.business_unit_id
             OR jl.cost_center_id IS DISTINCT FROM p.cost_center_id
             OR jl.project_id IS DISTINCT FROM p.project_id
             OR jl.segment_id IS DISTINCT FROM p.segment_id
           ))
       +
       (SELECT count(*)
          FROM gl.journal_entry_line jl
          LEFT JOIN gl.posted_ledger_line p
            ON p.organization_id = target.organization_id
           AND p.journal_entry_id = jl.journal_entry_id
           AND p.journal_line_id = jl.line_id
         WHERE jl.journal_entry_id = target.journal_entry_id
           AND p.ledger_line_id IS NULL) AS mismatched_rows
FROM live_fee target
WHERE target.key_namespace = 'JOURNAL_KEYED';
CREATE UNIQUE INDEX ON target_journal_parity (journal_entry_id);

CREATE TEMP TABLE candidate_schedule AS
SELECT target.statement_line_id,
       target.journal_entry_id AS target_journal_entry_id,
       target.journal_number AS target_journal_number,
       target.posting_batch_id AS target_posting_batch_id,
       target.idempotency_key AS target_idempotency_key,
       canonical.journal_entry_id AS canonical_journal_entry_id,
       canonical.journal_number AS canonical_journal_number,
       target.posting_date AS target_posting_date,
       target.fiscal_period_id AS target_fiscal_period_id,
       target.currency_code,
       target.total_debit_functional,
       mapping.mapping_name,
       mapping.canonical_bank_code,
       mapping.legacy_bank_code,
       mapping.expected_statement_line_resolves,
       target.header_signature = canonical.header_signature AS header_match,
       target.non_account_signature = canonical.non_account_signature
         AS non_account_effect_match,
       (SELECT effect_signature FROM ledger_account_effect
         WHERE journal_entry_id = target.journal_entry_id
           AND account_code = '6080')
       =
       (SELECT effect_signature FROM ledger_account_effect
         WHERE journal_entry_id = canonical.journal_entry_id
           AND account_code = '6080') AS expense_leg_match,
       (SELECT effect_signature FROM ledger_account_effect
         WHERE journal_entry_id = target.journal_entry_id
           AND account_code = mapping.legacy_bank_code)
       =
       (SELECT effect_signature FROM ledger_account_effect
         WHERE journal_entry_id = canonical.journal_entry_id
           AND account_code = mapping.canonical_bank_code)
         AS mapped_bank_leg_match,
       (target.ledger_entry_date_matches_header
        AND target.ledger_posting_date_matches_header
        AND target.ledger_period_matches_header
        AND target.first_ledger_posting_date = target.last_ledger_posting_date
        AND target.first_ledger_period_id = target.last_ledger_period_id)
         AS target_timing_matches_header,
       (canonical.ledger_entry_date_matches_header
        AND canonical.first_ledger_posting_date = canonical_timing.posting_date
        AND canonical.last_ledger_posting_date = canonical_timing.posting_date
        AND canonical.first_ledger_period_id = canonical_timing.fiscal_period_id
        AND canonical.last_ledger_period_id = canonical_timing.fiscal_period_id)
         AS canonical_timing_matches_approved_batch,
       target.first_ledger_posting_date AS target_ledger_posting_date,
       target.first_ledger_period_id AS target_ledger_period_id,
       canonical.first_ledger_posting_date AS canonical_ledger_posting_date,
       canonical.first_ledger_period_id AS canonical_ledger_period_id,
       canonical_timing.period_name AS canonical_ledger_period_name,
       parity.journal_rows,
       parity.ledger_rows,
       parity.mismatched_rows AS journal_ledger_mismatches,
       target.persisted_effect_signature,
       md5(target.persisted_effect_signature::text) AS target_effect_hash,
       md5(canonical.persisted_effect_signature::text)
         AS canonical_effect_hash,
       md5(concat_ws(
         '|', target.journal_entry_id::text,
         target.posting_batch_id::text,
         target.idempotency_key,
         canonical.journal_entry_id::text,
         mapping.mapping_name,
         mapping.canonical_bank_code,
         mapping.legacy_bank_code,
         mapping.expected_statement_line_resolves::text,
         target.first_ledger_posting_date::text,
         target.first_ledger_period_id::text,
         canonical.first_ledger_posting_date::text,
         canonical.first_ledger_period_id::text,
         :'REVERSAL_DATE'::date::text,
         period.fiscal_period_id::text,
         md5(target.persisted_effect_signature::text),
         md5(canonical.persisted_effect_signature::text)
       )) AS schedule_row_hash,
       period.fiscal_period_id AS reversal_fiscal_period_id,
       period.period_name AS reversal_period_name,
       :'REVERSAL_DATE'::date AS reversal_date,
       EXISTS (
         SELECT 1
         FROM banking.bank_statement_lines sl
         JOIN banking.bank_statements bs
           ON bs.statement_id = sl.statement_id
          AND bs.organization_id = target.organization_id
         WHERE sl.line_id = target.statement_line_id
       ) AS statement_line_resolves
FROM live_fee target
JOIN live_fee canonical
  ON canonical.statement_line_id = target.statement_line_id
 AND canonical.key_namespace = 'LINE_KEYED'
JOIN approved_account_mapping mapping
  ON canonical.account_code_multiset =
       jsonb_build_array(mapping.canonical_bank_code, '6080')
  OR canonical.account_code_multiset =
       jsonb_build_array('6080', mapping.canonical_bank_code)
JOIN target_journal_parity parity
  ON parity.journal_entry_id = target.journal_entry_id
CROSS JOIN correction_period period
CROSS JOIN approved_canonical_timing canonical_timing
WHERE target.key_namespace = 'JOURNAL_KEYED'
  AND (
    target.account_code_multiset =
      jsonb_build_array(mapping.legacy_bank_code, '6080')
    OR target.account_code_multiset =
      jsonb_build_array('6080', mapping.legacy_bank_code)
  );

-- This is the one-row-per-target plan. The primary/unique indexes are part of
-- the proof: no target, posting batch, or historical idempotency key can be
-- approved twice.
CREATE TEMP TABLE reversal_schedule AS
SELECT * FROM candidate_schedule;
CREATE UNIQUE INDEX ON reversal_schedule (target_journal_entry_id);
CREATE UNIQUE INDEX ON reversal_schedule (target_posting_batch_id);
CREATE UNIQUE INDEX ON reversal_schedule (target_idempotency_key);

\echo ''
\echo '===== W0a. Candidate admission predicates ====='
SELECT count(*) AS candidate_targets,
       count(*) FILTER (WHERE header_match) AS header_matches,
       count(*) FILTER (WHERE non_account_effect_match)
         AS non_account_effect_matches,
       count(*) FILTER (WHERE expense_leg_match) AS expense_leg_matches,
       count(*) FILTER (WHERE mapped_bank_leg_match) AS mapped_bank_leg_matches,
       count(*) FILTER (WHERE target_timing_matches_header)
         AS target_timing_matches,
       count(*) FILTER (WHERE canonical_timing_matches_approved_batch)
         AS canonical_timing_matches,
       count(*) FILTER (WHERE journal_rows = ledger_rows)
         AS equal_journal_ledger_counts,
       count(*) FILTER (WHERE journal_ledger_mismatches = 0)
         AS exact_journal_ledger_targets,
       count(*) FILTER (WHERE statement_line_resolves)
         AS resolved_statement_lines,
       count(*) FILTER (
         WHERE statement_line_resolves IS NOT DISTINCT FROM
               expected_statement_line_resolves
       ) AS expected_resolution_states
FROM reversal_schedule;

DO $schedule$
DECLARE all_targets integer;
DECLARE scheduled integer;
DECLARE affected integer;
DECLARE bad integer;
DECLARE gross numeric(20,6);
BEGIN
  SELECT COALESCE(sum(target_live), 0), count(*)
    INTO all_targets, affected
  FROM affected_line;
  SELECT count(*),
         count(*) FILTER (
           WHERE NOT header_match
              OR NOT non_account_effect_match
              OR NOT expense_leg_match
              OR NOT mapped_bank_leg_match
              OR NOT target_timing_matches_header
              OR NOT canonical_timing_matches_approved_batch
              OR journal_rows <> ledger_rows
              OR journal_ledger_mismatches <> 0
              OR statement_line_resolves IS DISTINCT FROM
                   expected_statement_line_resolves
         ),
         sum(total_debit_functional)
    INTO scheduled, bad, gross
  FROM reversal_schedule;

  IF all_targets <> 429 OR affected <> 39 THEN
    RAISE EXCEPTION
      'wrong-account population changed: expected 429 targets on 39 lines, found % on %',
      all_targets, affected;
  END IF;
  IF scheduled <> all_targets THEN
    RAISE EXCEPTION
      'wrong-account schedule incomplete: % live targets, % admitted by the approved mappings',
      all_targets, scheduled;
  END IF;
  IF bad <> 0 THEN
    RAISE EXCEPTION
      'wrong-account schedule refused: % targets fail header/effect/mapping/timing/journal-line/source-state proof', bad;
  END IF;
  IF gross <> 7764.680000 THEN
    RAISE EXCEPTION
      'wrong-account gross changed: expected 7764.680000, found %', gross;
  END IF;
  IF EXISTS (
    SELECT 1
    FROM approved_account_mapping m
    LEFT JOIN reversal_schedule s ON s.mapping_name = m.mapping_name
    GROUP BY m.mapping_name, m.expected_targets
    HAVING count(s.target_journal_entry_id) <> m.expected_targets
  ) THEN
    RAISE EXCEPTION
      'wrong-account mapping cardinality changed from the approved 352/77 split';
  END IF;
END
$schedule$;

-- A canary against the actual admission predicate. Each probe flips exactly
-- one required fact; only the unchanged control may remain admissible.
CREATE TEMP TABLE admission_canary AS
WITH fixture AS (
  SELECT header_match AS header_ok,
         non_account_effect_match AS economic_effect_ok,
         expense_leg_match AND mapped_bank_leg_match AS account_mapping_ok,
         target_timing_matches_header
           AND canonical_timing_matches_approved_batch AS timing_ok,
         journal_rows = ledger_rows
           AND journal_ledger_mismatches = 0 AS journal_parity_ok,
         statement_line_resolves IS NOT DISTINCT FROM
           expected_statement_line_resolves AS source_state_ok,
         mapping_name IN (
           'PAYSTACK_OPEX_LEGACY_TO_1211', 'ZENITH_USD_LEGACY_TO_1207'
         ) AS mapping_vocabulary_ok,
         reversal_date = :'REVERSAL_DATE'::date
           AND reversal_fiscal_period_id = (
             SELECT fiscal_period_id FROM correction_period
           ) AS reversal_period_ok
  FROM reversal_schedule
  ORDER BY target_journal_entry_id
  LIMIT 1
), probes AS (
  SELECT 'unchanged control' AS probe, f.*, true AS expected FROM fixture f
  UNION ALL
  SELECT 'header', false, economic_effect_ok, account_mapping_ok, timing_ok,
         journal_parity_ok, source_state_ok, mapping_vocabulary_ok,
         reversal_period_ok, false
  FROM fixture
  UNION ALL
  SELECT 'non-account amount', header_ok, false, account_mapping_ok, timing_ok,
         journal_parity_ok, source_state_ok, mapping_vocabulary_ok,
         reversal_period_ok, false
  FROM fixture
  UNION ALL
  SELECT 'account mapping', header_ok, economic_effect_ok, false, timing_ok,
         journal_parity_ok, source_state_ok, mapping_vocabulary_ok,
         reversal_period_ok, false
  FROM fixture
  UNION ALL
  SELECT 'ledger timing', header_ok, economic_effect_ok, account_mapping_ok,
         false, journal_parity_ok, source_state_ok, mapping_vocabulary_ok,
         reversal_period_ok, false
  FROM fixture
  UNION ALL
  SELECT 'journal-line parity', header_ok, economic_effect_ok,
         account_mapping_ok, timing_ok, false, source_state_ok,
         mapping_vocabulary_ok, reversal_period_ok, false
  FROM fixture
  UNION ALL
  SELECT 'source resolution state', header_ok, economic_effect_ok,
         account_mapping_ok, timing_ok, journal_parity_ok, false,
         mapping_vocabulary_ok, reversal_period_ok, false
  FROM fixture
  UNION ALL
  SELECT 'reversal period', header_ok, economic_effect_ok,
         account_mapping_ok, timing_ok, journal_parity_ok, source_state_ok,
         mapping_vocabulary_ok, false, false
  FROM fixture
)
SELECT probe,
       (header_ok AND economic_effect_ok AND account_mapping_ok AND timing_ok
        AND journal_parity_ok AND source_state_ok AND mapping_vocabulary_ok
        AND reversal_period_ok) AS observed,
       expected
FROM probes;

DO $sensitivity$
DECLARE bad integer;
BEGIN
  SELECT count(*) INTO bad
  FROM admission_canary
  WHERE observed IS DISTINCT FROM expected;
  IF bad <> 0 THEN
    RAISE EXCEPTION
      'wrong-account admission sensitivity proof failed on % probes', bad;
  END IF;
  RAISE NOTICE
    'wrong-account admission sensitivity PASSED: header, amount, mapping, timing, journal-line, source-state and reversal-period changes are refused';
END
$sensitivity$;

\echo ''
\echo '===== W1. Exact approved account substitutions ====='
SELECT s.mapping_name, s.legacy_bank_code, s.canonical_bank_code,
       count(*) AS reversal_targets,
       sum(s.total_debit_functional) AS gross,
       count(*) FILTER (WHERE s.statement_line_resolves)
         AS source_lines_resolved,
       count(*) FILTER (WHERE NOT s.statement_line_resolves)
         AS source_lines_dangling
FROM reversal_schedule s
GROUP BY s.mapping_name, s.legacy_bank_code, s.canonical_bank_code
ORDER BY s.mapping_name;

\echo ''
\echo '===== W2. Journal source lines reproduce immutable target effects ====='
SELECT count(*) AS reversal_targets,
       sum(journal_rows) AS journal_rows,
       sum(ledger_rows) AS ledger_rows,
       sum(journal_ledger_mismatches) AS mismatched_rows,
       count(*) FILTER (WHERE journal_rows = ledger_rows
                          AND journal_ledger_mismatches = 0) AS exact_targets
FROM reversal_schedule;

\echo ''
\echo '===== W3. Finance approval binding ====='
SELECT count(*) AS reversal_targets,
       count(DISTINCT statement_line_id) AS affected_statement_lines,
       sum(total_debit_functional) AS gross,
       count(*) FILTER (WHERE statement_line_resolves) AS source_lines_resolved,
       count(*) FILTER (WHERE NOT statement_line_resolves)
         AS source_lines_dangling,
       md5(COALESCE(string_agg(
         schedule_row_hash, ',' ORDER BY target_journal_entry_id), ''))
         AS schedule_digest,
       min(target_ledger_posting_date) AS first_target_ledger_posting_date,
       max(target_ledger_posting_date) AS last_target_ledger_posting_date,
       canonical_ledger_posting_date,
       canonical_ledger_period_id,
       canonical_ledger_period_name,
       reversal_date,
       reversal_fiscal_period_id,
       reversal_period_name
FROM reversal_schedule
GROUP BY canonical_ledger_posting_date, canonical_ledger_period_id,
         canonical_ledger_period_name, reversal_date,
         reversal_fiscal_period_id, reversal_period_name;

\echo ''
\echo '===== W3a. Every target named — keep this output inside Finance ====='
SELECT target_journal_entry_id, target_journal_number,
       target_posting_batch_id, target_idempotency_key,
       statement_line_id, canonical_journal_entry_id,
       canonical_journal_number, mapping_name,
       legacy_bank_code, canonical_bank_code,
       statement_line_resolves, expected_statement_line_resolves,
       total_debit_functional, target_effect_hash, canonical_effect_hash,
       target_ledger_posting_date, target_ledger_period_id,
       canonical_ledger_posting_date, canonical_ledger_period_id,
       reversal_date, reversal_fiscal_period_id, schedule_row_hash
FROM reversal_schedule
ORDER BY target_journal_entry_id;

\echo ''
\echo '===== W4. Simulated correcting-reversal effect by account ====='
CREATE TEMP TABLE simulated_correction AS
SELECT s.target_journal_entry_id,
       p.account_id, p.account_code,
       p.credit_amount AS correcting_debit,
       p.debit_amount AS correcting_credit,
       s.reversal_date, s.reversal_fiscal_period_id
FROM reversal_schedule s
JOIN gl.posted_ledger_line p
  ON p.organization_id = :'ORG'::uuid
 AND p.journal_entry_id = s.target_journal_entry_id;

SELECT account_code,
       sum(correcting_debit) AS correcting_debit,
       sum(correcting_credit) AS correcting_credit
FROM simulated_correction
GROUP BY account_code
ORDER BY account_code;

\echo ''
\echo '===== W5. Simulated all-time postconditions ====='
CREATE TEMP TABLE simulated_net AS
SELECT s.target_journal_entry_id, p.account_id,
       sum(p.debit_amount - p.credit_amount)
       + sum(c.correcting_debit - c.correcting_credit) AS net_effect
FROM reversal_schedule s
JOIN gl.posted_ledger_line p
  ON p.organization_id = :'ORG'::uuid
 AND p.journal_entry_id = s.target_journal_entry_id
JOIN simulated_correction c
  ON c.target_journal_entry_id = s.target_journal_entry_id
 AND c.account_id = p.account_id
GROUP BY s.target_journal_entry_id, p.account_id;

DO $postconditions$
DECLARE nonzero integer;
DECLARE unbalanced integer;
DECLARE canonical_missing integer;
BEGIN
  SELECT count(*) INTO nonzero FROM simulated_net WHERE net_effect <> 0;
  SELECT count(*) INTO unbalanced
  FROM (
    SELECT target_journal_entry_id
    FROM simulated_correction
    GROUP BY target_journal_entry_id
    HAVING sum(correcting_debit) <> sum(correcting_credit)
  ) q;
  SELECT count(*) INTO canonical_missing
  FROM reversal_schedule s
  LEFT JOIN live_fee c
    ON c.journal_entry_id = s.canonical_journal_entry_id
   AND c.key_namespace = 'LINE_KEYED'
  WHERE c.journal_entry_id IS NULL;

  IF nonzero <> 0 OR unbalanced <> 0 OR canonical_missing <> 0 THEN
    RAISE EXCEPTION
      'simulated correction refused: % nonzero target/account nets, % unbalanced reversals, % missing live canonicals',
      nonzero, unbalanced, canonical_missing;
  END IF;
END
$postconditions$;

SELECT count(DISTINCT target_journal_entry_id) AS linked_reversals,
       count(*) FILTER (WHERE net_effect = 0) AS zeroed_target_account_effects,
       (SELECT count(*) FROM reversal_schedule) AS targets_removed_from_live_predicate,
       (SELECT count(DISTINCT canonical_journal_entry_id)
          FROM reversal_schedule) AS canonical_journals_retained
FROM simulated_net;

ROLLBACK;
