-- ===========================================================================
-- Gate G detector — the 12,224 APPROVED journals outside the ar/INVOICE cohort.
--
-- Read-only: one transaction, temp tables only, ending in ROLLBACK. Run against
-- an ISOLATED RESTORED DATABASE, never a production instance.
--
-- WHAT THIS IS FOR
--
-- Gate G blocks legacy-writer retirement until every remaining APPROVED journal
-- has an owned disposition (`docs/architecture/accounting-adoption-boundary.md`).
-- Memorandum §5 sets the classification policy; this script produces the
-- ledger-decidable evidence for it, per cohort. It decides nothing about a
-- source document's business validity — that stays Finance's.
--
-- WHY IT IS NOT ONE QUERY
--
-- These five cohorts are not one population and must never be dispositioned as
-- one. They differ in how the journal is tied to its source:
--
--   CUSTOMER_PAYMENT / EXPENSE_REIMBURSEMENT / PAYROLL_ENTRY (101 journals)
--       carry `source_document_id`. Real document identity.
--
--   BANK_FEE (12,117 journals)
--       carry NO `source_document_id` — but the writers DO record the exact
--       statement line as `correlation_id = "bank-fee-<line_id>"`. This script
--       parses that UUID and joins `banking.bank_statement_lines`. Exact
--       identity, not a heuristic.
--
--   BANK_RECONCILIATION (6 journals)
--       carry no document id, no reference and no correlation id. They are
--       HEADER-unlinked. `bank_statement_line_matches` is searched on the
--       JOURNAL LINE id before any of them is called unlinkable.
--
-- A HEURISTIC KEY WAS TRIED FIRST AND IS WITHDRAWN. An earlier version grouped
-- fees by reference + date + amount + bank account and reported "111 fee
-- events" and a "V2 VOID" disposition. Those buckets merged distinct statement
-- lines — 111 buckets covered 149 real lines — and the merge manufactured a
-- false duplicate-posting finding. No heuristic grouping survives in this
-- script. If exact identity is unavailable for a row, the row is quarantined,
-- not bucketed.
--
-- WHAT IT DELIBERATELY DOES NOT DO
--
-- It never uses a net-effect signature match ACROSS documents as evidence. In
-- this ledger 96% of journals share their signature with another (see the
-- ar/INVOICE detector, §12 of its output), so a cross-document match is
-- collision, not proof. Signatures here only ever compare journals already tied
-- to the SAME document or the SAME fee event.
--
-- Every result is as of the copy it runs against. A disposition must be
-- revalidated against current authoritative state immediately before execution.
-- ===========================================================================

\set ON_ERROR_STOP on
\timing on
\pset pager off
\set ORG '00000000-0000-0000-0000-000000000001'

BEGIN;

-- ---------------------------------------------------------------------------
-- SENSITIVITY CANARY — inserted BEFORE `candidate` is materialized, so the
-- assertion tests THE DETECTOR'S OWN PREDICATE and not two queries written
-- beside it. Rolled back with everything else.
-- ---------------------------------------------------------------------------
INSERT INTO gl.journal_entry
  (journal_entry_id, organization_id, journal_number, entry_date, posting_date,
   status, is_reversal, source_module, source_document_type, total_debit_functional)
VALUES
  ('00000000-0000-0000-0000-0000000000ff'::uuid,
   '00000000-0000-0000-0000-0000000000aa'::uuid,
   'SENSITIVITY-CANARY', DATE '2026-01-01', DATE '2026-01-01',
   'APPROVED', false, 'BANKING', 'BANK_FEE', 1.000000);

CREATE TEMP TABLE candidate AS
SELECT je.journal_entry_id, je.journal_number, je.source_module,
       je.source_document_type, je.source_document_id, je.reference,
       je.correlation_id, je.entry_date, je.posting_date,
       je.total_debit_functional, je.fiscal_period_id
FROM gl.journal_entry je
WHERE je.organization_id = :'ORG'::uuid
  AND je.status = 'APPROVED'
  AND NOT (je.source_module = 'AR' AND je.source_document_type = 'INVOICE');
CREATE INDEX ON candidate (source_document_id);
CREATE INDEX ON candidate (reference);

\echo ''
\echo '===== 0b. SENSITIVITY PROOF: the organization predicate actually bites ====='
CREATE TEMP TABLE canary_check AS
SELECT
  (SELECT count(*) FROM gl.journal_entry WHERE journal_number='SENSITIVITY-CANARY') AS canary_in_source,
  (SELECT count(*) FROM candidate WHERE journal_number='SENSITIVITY-CANARY')        AS canary_in_candidate,
  (SELECT count(*) FROM gl.journal_entry
     WHERE status='APPROVED'
       AND NOT (source_module='AR' AND source_document_type='INVOICE'))             AS unscoped_count,
  (SELECT count(*) FROM candidate)                                                  AS detector_population;
SELECT * FROM canary_check;

DO $canary$
DECLARE c record;
BEGIN
  SELECT * INTO c FROM canary_check;
  IF c.canary_in_source <> 1 THEN
    RAISE EXCEPTION 'sensitivity proof INVALID: canary not in source (found %)', c.canary_in_source;
  END IF;
  IF c.canary_in_candidate <> 0 THEN
    RAISE EXCEPTION 'ORGANIZATION PREDICATE FAILED: the second-tenant canary reached `candidate` (% rows). Every count in this run would be cross-tenant.', c.canary_in_candidate;
  END IF;
  IF c.unscoped_count <> c.detector_population + 1 THEN
    RAISE EXCEPTION 'ORGANIZATION PREDICATE NOT EXERCISED: unscoped=% but population=%; expected exactly one more.', c.unscoped_count, c.detector_population;
  END IF;
  RAISE NOTICE 'sensitivity proof PASSED: canary present in source, absent from candidate, unscoped = scoped + 1';
END
$canary$;

DELETE FROM gl.journal_entry WHERE journal_number = 'SENSITIVITY-CANARY';
DO $gone$
DECLARE n integer;
BEGIN
  SELECT count(*) INTO n FROM gl.journal_entry WHERE journal_number='SENSITIVITY-CANARY';
  IF n <> 0 THEN RAISE EXCEPTION 'canary was not removed (% rows remain)', n; END IF;
END
$gone$;

\echo ''
\echo '===== 0. The Gate G population by cohort ====='
SELECT source_module, source_document_type, count(*) AS journals,
       sum(total_debit_functional) AS gross_debit,
       count(source_document_id) AS with_document_id,
       count(reference) AS with_reference,
       min(entry_date) AS first_entry, max(entry_date) AS last_entry
FROM candidate GROUP BY 1,2 ORDER BY 3 DESC;

-- ---------------------------------------------------------------------------
-- Net effect per journal, for same-document / same-event comparison ONLY.
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE je_sig AS
SELECT journal_entry_id,
       string_agg(account_id::text||':'||trim_scale(net)::text, ',' ORDER BY account_id) AS fwd
FROM (SELECT journal_entry_id, account_id,
             sum(debit_amount_functional - credit_amount_functional) AS net
      FROM gl.journal_entry_line GROUP BY 1,2) a
WHERE net <> 0 GROUP BY 1;
CREATE UNIQUE INDEX ON je_sig (journal_entry_id);

\echo ''
\echo '===== 1. MECHANICAL POSTABILITY — what the existing backlog service sees ====='
\echo '-- `gl.posting_backlog.post_approved_journals` posts an APPROVED journal when'
\echo '-- it is balanced and its period accepts posting. That is the whole test it'
\echo '-- applies. This section is how many of these it would post TODAY.'
SELECT c.source_document_type,
       count(*) AS journals,
       count(*) FILTER (WHERE b.imbalance = 0) AS balanced,
       count(*) FILTER (WHERE COALESCE(fp.status,'(none)') IN ('OPEN','REOPENED','(none)')) AS period_accepts,
       count(*) FILTER (WHERE b.imbalance = 0
                          AND COALESCE(fp.status,'(none)') IN ('OPEN','REOPENED','(none)')) AS would_be_posted,
       sum(c.total_debit_functional) FILTER (WHERE b.imbalance = 0
                          AND COALESCE(fp.status,'(none)') IN ('OPEN','REOPENED','(none)')) AS gross_it_would_post
FROM candidate c
LEFT JOIN gl.fiscal_period fp ON fp.fiscal_period_id = c.fiscal_period_id
LEFT JOIN (SELECT journal_entry_id,
                  sum(debit_amount_functional) - sum(credit_amount_functional) AS imbalance
           FROM gl.journal_entry_line GROUP BY 1) b ON b.journal_entry_id = c.journal_entry_id
GROUP BY 1 ORDER BY 2 DESC;

-- ---------------------------------------------------------------------------
-- STRONG EVIDENCE: cohorts with a real document id.
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE linked AS
SELECT c.journal_entry_id, c.journal_number, c.source_document_type,
       c.source_document_id, c.total_debit_functional, c.posting_date,
       EXISTS (SELECT 1 FROM gl.journal_entry o
               WHERE o.organization_id = :'ORG'::uuid
                 AND o.source_document_id = c.source_document_id
                 AND o.source_document_type = c.source_document_type
                 AND o.status = 'POSTED')                                AS document_has_posted_journal,
       EXISTS (SELECT 1 FROM gl.journal_entry o
               JOIN je_sig os ON os.journal_entry_id = o.journal_entry_id
               JOIN je_sig cs ON cs.journal_entry_id = c.journal_entry_id
               WHERE o.organization_id = :'ORG'::uuid
                 AND o.source_document_id = c.source_document_id
                 AND o.source_document_type = c.source_document_type
                 AND o.status = 'POSTED'
                 AND os.fwd = cs.fwd)                                    AS posted_effect_is_identical
FROM candidate c
WHERE c.source_document_id IS NOT NULL;

\echo ''
\echo '===== 2. DOCUMENT-LINKED cohorts (101) — strong evidence ====='
SELECT source_document_type, count(*) AS journals,
       count(*) FILTER (WHERE document_has_posted_journal) AS document_already_posted,
       count(*) FILTER (WHERE posted_effect_is_identical)  AS posted_effect_identical,
       count(*) FILTER (WHERE document_has_posted_journal
                          AND NOT posted_effect_is_identical) AS posted_but_effect_differs,
       count(*) FILTER (WHERE NOT document_has_posted_journal) AS nothing_posted_yet,
       sum(total_debit_functional) AS gross_debit
FROM linked GROUP BY 1 ORDER BY 2 DESC;

\echo ''
\echo '===== 2b. Document-linked journals needing adjudication, identified ====='
SELECT journal_number, source_document_type, posting_date, total_debit_functional,
       document_has_posted_journal, posted_effect_is_identical
FROM linked
WHERE NOT posted_effect_is_identical
ORDER BY total_debit_functional DESC;

-- ---------------------------------------------------------------------------
-- EXACT EVIDENCE: bank fees, by statement-line identity.
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE fee AS
SELECT c.journal_entry_id, c.journal_number, c.total_debit_functional,
       CASE WHEN c.correlation_id ~ '^bank-fee-[0-9a-fA-F-]{36}$'
            THEN substring(c.correlation_id from 10)::uuid END AS line_id
FROM candidate c
WHERE c.source_document_type = 'BANK_FEE';
CREATE INDEX ON fee (line_id);

-- The POSTED journal on each line, with everything needed to judge whether its
-- effect is CURRENTLY EFFECTIVE — not merely present.
CREATE TEMP TABLE posted_fee AS
SELECT je.journal_entry_id, je.journal_number, je.currency_code,
       je.fiscal_period_id, je.is_reversal, je.reversal_journal_id,
       substring(je.correlation_id from 10)::uuid AS line_id,
       (SELECT count(*) FROM gl.posted_ledger_line p
         WHERE p.journal_entry_id = je.journal_entry_id) AS ledger_rows
FROM gl.journal_entry je
WHERE je.organization_id = :'ORG'::uuid AND je.status = 'POSTED'
  AND je.source_document_type = 'BANK_FEE'
  AND je.correlation_id ~ '^bank-fee-[0-9a-fA-F-]{36}$';
CREATE INDEX ON posted_fee (line_id);

\echo ''
\echo '===== 3. BANK_FEE — statement-line identity health ====='
SELECT count(*) AS approved_journals,
       count(line_id) AS parsed_a_line_uuid,
       count(*) FILTER (WHERE line_id IS NULL) AS unparseable_correlation_id,
       count(DISTINCT line_id) AS distinct_statement_lines,
       count(DISTINCT line_id) FILTER (WHERE EXISTS (
         SELECT 1 FROM banking.bank_statement_lines sl WHERE sl.line_id = fee.line_id)) AS lines_that_resolve
FROM fee;

\echo ''
\echo '===== 3b. The true fee value of those lines, against what posting would book ====='
WITH l AS (SELECT DISTINCT line_id FROM fee WHERE line_id IS NOT NULL)
SELECT count(*) AS statement_lines,
       sum(abs(sl.amount)) AS true_fee_value_once,
       (SELECT sum(total_debit_functional) FROM fee) AS gross_if_every_journal_posted,
       round((SELECT sum(total_debit_functional) FROM fee) / sum(abs(sl.amount)), 1) AS inflation_factor
FROM l JOIN banking.bank_statement_lines sl ON sl.line_id = l.line_id;

\echo ''
\echo '===== 4. H1 ECONOMIC EQUIVALENCE — APPROVED vs the POSTED journal on its line ====='
\echo '-- Exact line identity proves ASSOCIATION and CARDINALITY. It does not prove'
\echo '-- the two journals have the same effect. That is what this section tests.'
CREATE TEMP TABLE h1 AS
SELECT f.journal_entry_id, f.journal_number, f.total_debit_functional, f.line_id,
       fs.fwd  AS approved_sig, ps.fwd AS posted_sig,
       aj.currency_code AS approved_ccy, p.currency_code AS posted_ccy,
       aj.fiscal_period_id AS approved_period, p.fiscal_period_id AS posted_period,
       p.journal_entry_id AS posted_id, p.is_reversal AS posted_is_reversal,
       p.reversal_journal_id AS posted_has_reversal, p.ledger_rows
FROM fee f
JOIN gl.journal_entry aj ON aj.journal_entry_id = f.journal_entry_id
LEFT JOIN je_sig fs ON fs.journal_entry_id = f.journal_entry_id
LEFT JOIN posted_fee p ON p.line_id = f.line_id
LEFT JOIN je_sig ps ON ps.journal_entry_id = p.journal_entry_id;

SELECT count(*) AS approved_journals,
       count(posted_id)                                              AS have_a_posted_journal_on_their_line,
       count(*) FILTER (WHERE approved_sig = posted_sig)              AS same_net_effect_by_account,
       count(*) FILTER (WHERE approved_ccy = posted_ccy)              AS same_currency,
       count(*) FILTER (WHERE approved_period = posted_period)        AS same_fiscal_period,
       count(*) FILTER (WHERE NOT posted_is_reversal)                 AS posted_is_not_a_reversal,
       count(*) FILTER (WHERE posted_has_reversal IS NULL)            AS posted_not_since_reversed,
       count(*) FILTER (WHERE ledger_rows > 0)                        AS posted_has_ledger_rows
FROM h1;

-- ---------------------------------------------------------------------------
-- THE DISPOSITION. Exact identity only; no heuristic bucket anywhere.
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE disposition AS
SELECT c.journal_entry_id, c.journal_number, c.source_document_type,
       c.total_debit_functional,
       CASE
         WHEN l.journal_entry_id IS NOT NULL AND l.posted_effect_is_identical
           THEN 'V1 VOID candidate - identical effect already posted on the same document'
         WHEN l.journal_entry_id IS NOT NULL AND l.document_has_posted_journal
           THEN 'Q1 QUARANTINE - document already posted but the effect differs'
         WHEN l.journal_entry_id IS NOT NULL
           THEN 'P1 POST CANDIDATE - document linked, nothing posted yet'
         WHEN h.journal_entry_id IS NOT NULL AND h.posted_id IS NULL
           THEN 'Q3 QUARANTINE - statement line has no POSTED journal'
         WHEN h.journal_entry_id IS NOT NULL
              AND h.approved_sig = h.posted_sig
              AND h.approved_ccy = h.posted_ccy
              AND NOT h.posted_is_reversal
              AND h.posted_has_reversal IS NULL
              AND h.ledger_rows > 0
           THEN 'H1A VOID candidate - same line, same effect, currently effective'
         WHEN h.journal_entry_id IS NOT NULL
           THEN 'H1B QUARANTINE - same line, effect or effectiveness differs'
         WHEN c.source_document_type = 'BANK_FEE'
           THEN 'Q4 QUARANTINE - bank fee with no parseable statement-line id'
         ELSE 'Q2 QUARANTINE - header-unlinked; follow bank_statement_line_matches first'
       END AS disposition
FROM candidate c
LEFT JOIN linked l ON l.journal_entry_id = c.journal_entry_id
LEFT JOIN h1 h ON h.journal_entry_id = c.journal_entry_id;

\echo ''
\echo '===== 5. THE DISPOSITION — every candidate classified on exact identity ====='
SELECT disposition, count(*) AS journals, sum(total_debit_functional) AS gross_debit
FROM disposition GROUP BY 1
UNION ALL SELECT 'TOTAL', count(*), sum(total_debit_functional) FROM candidate
ORDER BY 1;

\echo ''
\echo '===== 5b. Disposition by cohort — these are separate decisions ====='
SELECT source_document_type, disposition, count(*) AS journals,
       sum(total_debit_functional) AS gross_debit
FROM disposition GROUP BY 1,2 ORDER BY 3 DESC;

\echo ''
\echo '===== 5c. The two legacy Paystack-account rows, held out ====='
\echo '-- Their line''s bank account is Paystack OPEX; the journals credit the legacy'
\echo '-- `Paystack OPEX - DT` code. Dispose of them separately from the rest.'
SELECT d.journal_number, d.total_debit_functional, d.disposition
FROM disposition d WHERE d.journal_number IN ('JE202603-0227','JE202603-0230');

\echo ''
\echo '===== 6. Effect on the ledger if the whole population were posted ====='
\echo '-- Not a proposal. This is the size of the mistake the mechanical path makes.'
SELECT d.source_document_type, a.account_code, a.account_name,
       count(DISTINCT d.journal_entry_id) AS journals,
       sum(l.debit_amount_functional - l.credit_amount_functional) AS net_debit_effect
FROM disposition d
JOIN gl.journal_entry_line l ON l.journal_entry_id = d.journal_entry_id
JOIN gl.account a ON a.account_id = l.account_id
GROUP BY 1,2,3 ORDER BY 1, abs(sum(l.debit_amount_functional - l.credit_amount_functional)) DESC;

\echo ''
\echo '===== 7. Quarantine items, identified ====='
SELECT journal_number, source_document_type, total_debit_functional, disposition
FROM disposition WHERE disposition LIKE 'Q%'
ORDER BY total_debit_functional DESC;

\echo ''
\echo '===== 8. Post candidates, identified ====='
SELECT journal_number, source_document_type, total_debit_functional, disposition
FROM disposition WHERE disposition LIKE 'P%'
ORDER BY total_debit_functional DESC LIMIT 50;

\echo ''
ROLLBACK;
