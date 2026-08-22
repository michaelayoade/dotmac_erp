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
-- one. They differ in the only thing that matters here — WHETHER THE JOURNAL CAN
-- BE TIED TO A SOURCE DOCUMENT AT ALL:
--
--   CUSTOMER_PAYMENT / EXPENSE_REIMBURSEMENT / PAYROLL_ENTRY (101 journals)
--       carry `source_document_id`. Real document identity. Strong evidence.
--
--   BANK_FEE (12,117 journals)
--       carry NO `source_document_id`. They can only be grouped by a HEURISTIC
--       KEY — reference + entry date + amount + bank account — corroborated by
--       the reference resolving to a `banking.bank_statement_lines` row. That is
--       weaker, and section 12 quantifies how much weaker.
--
--   BANK_RECONCILIATION (6 journals)
--       carry no document id, no reference and no correlation id. They cannot be
--       tied to anything by any ledger query. They are UNDECIDABLE here, and
--       saying so is the finding.
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
-- HEURISTIC EVIDENCE: bank fees, grouped by fee event.
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE fee AS
SELECT c.journal_entry_id, c.journal_number, c.reference, c.entry_date,
       c.total_debit_functional,
       (SELECT min(a.account_code) FROM gl.journal_entry_line l
          JOIN gl.account a ON a.account_id = l.account_id
         WHERE l.journal_entry_id = c.journal_entry_id
           AND l.credit_amount_functional > 0)                           AS bank_account
FROM candidate c
WHERE c.source_document_type = 'BANK_FEE';
CREATE INDEX ON fee (reference, entry_date, total_debit_functional, bank_account);

CREATE TEMP TABLE posted_fee AS
SELECT je.journal_entry_id, je.reference, je.entry_date, je.total_debit_functional,
       (SELECT min(a.account_code) FROM gl.journal_entry_line l
          JOIN gl.account a ON a.account_id = l.account_id
         WHERE l.journal_entry_id = je.journal_entry_id
           AND l.credit_amount_functional > 0)                           AS bank_account
FROM gl.journal_entry je
WHERE je.organization_id = :'ORG'::uuid AND je.status = 'POSTED'
  AND je.source_document_type = 'BANK_FEE';
CREATE INDEX ON posted_fee (reference, entry_date, total_debit_functional, bank_account);

\echo ''
\echo '===== 3. BANK_FEE — how many distinct fee events are these journals about? ====='
SELECT count(*) AS approved_journals,
       count(DISTINCT (reference, entry_date, total_debit_functional, bank_account)) AS distinct_fee_events,
       sum(total_debit_functional) AS gross_if_every_journal_posted,
       (SELECT sum(t.total_debit_functional)
          FROM (SELECT DISTINCT reference, entry_date, total_debit_functional, bank_account
                FROM fee) t)                                            AS value_of_the_events_themselves
FROM fee;

\echo ''
\echo '===== 3b. Multiplicity — approved journals per fee event ====='
WITH g AS (SELECT reference, entry_date, total_debit_functional, bank_account, count(*) AS n
           FROM fee GROUP BY 1,2,3,4)
SELECT n AS approved_journals_on_one_event, count(*) AS events, sum(n) AS journals
FROM g GROUP BY 1 ORDER BY 1 DESC LIMIT 12;

\echo ''
\echo '===== 4. BANK_FEE — is the event already posted? ====='
SELECT count(*) AS approved_journals,
       count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM posted_fee p
         WHERE p.reference = f.reference AND p.entry_date = f.entry_date
           AND p.total_debit_functional = f.total_debit_functional
           AND p.bank_account IS NOT DISTINCT FROM f.bank_account))      AS event_already_posted,
       sum(f.total_debit_functional) FILTER (WHERE EXISTS (
         SELECT 1 FROM posted_fee p
         WHERE p.reference = f.reference AND p.entry_date = f.entry_date
           AND p.total_debit_functional = f.total_debit_functional
           AND p.bank_account IS NOT DISTINCT FROM f.bank_account))      AS gross_already_posted
FROM fee f;

\echo ''
\echo '===== 4b. Corroboration — do the fee references resolve to a statement line? ====='
\echo '-- The fees carry no document id. If the reference resolves to the banking'
\echo '-- subledger the grouping is at least anchored to a real bank event.'
SELECT count(*) AS distinct_references,
       count(*) FILTER (WHERE EXISTS (
         SELECT 1 FROM banking.bank_statement_lines sl
         WHERE sl.reference = r.reference OR sl.description = r.reference)) AS resolve_to_a_statement_line
FROM (SELECT DISTINCT reference FROM fee WHERE reference IS NOT NULL) r;

\echo ''
\echo '===== 4c. Pre-existing duplication in the POSTED bank fees ====='
\echo '-- A separate finding: the POSTED side is not clean either.'
WITH ev AS (SELECT DISTINCT reference, entry_date, total_debit_functional, bank_account FROM fee)
SELECT count(*) AS fee_events,
       sum((SELECT count(*) FROM posted_fee p
            WHERE p.reference = ev.reference AND p.entry_date = ev.entry_date
              AND p.total_debit_functional = ev.total_debit_functional
              AND p.bank_account IS NOT DISTINCT FROM ev.bank_account)) AS posted_journals_on_them
FROM ev;

\echo ''
\echo '===== 5. THE DISPOSITION — every one of the 12,224 classified ====='
CREATE TEMP TABLE disposition AS
SELECT c.journal_entry_id, c.journal_number, c.source_document_type,
       c.total_debit_functional,
       CASE
         WHEN l.journal_entry_id IS NOT NULL AND l.posted_effect_is_identical
           THEN 'V1 VOID - identical effect already posted on the same document'
         WHEN l.journal_entry_id IS NOT NULL AND l.document_has_posted_journal
           THEN 'Q1 QUARANTINE - document already posted but the effect differs'
         WHEN l.journal_entry_id IS NOT NULL
           THEN 'P1 POST CANDIDATE - document linked, nothing posted yet'
         WHEN c.source_document_type = 'BANK_FEE' AND EXISTS (
                SELECT 1 FROM posted_fee p JOIN fee f ON f.journal_entry_id = c.journal_entry_id
                WHERE p.reference = f.reference AND p.entry_date = f.entry_date
                  AND p.total_debit_functional = f.total_debit_functional
                  AND p.bank_account IS NOT DISTINCT FROM f.bank_account)
           THEN 'V2 VOID - fee event already posted (HEURISTIC key, see s12)'
         WHEN c.source_document_type = 'BANK_FEE'
           THEN 'P2 POST CANDIDATE - fee event not posted (HEURISTIC key)'
         ELSE 'Q2 QUARANTINE - no linkage of any kind; not decidable from the ledger'
       END AS disposition
FROM candidate c
LEFT JOIN linked l ON l.journal_entry_id = c.journal_entry_id;

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
\echo '===== 12. EVIDENCE STRENGTH — the heuristic key, quantified ====='
\echo '-- `reference` ALONE is not a document key. This is why the key used above'
\echo '-- is reference + date + amount + bank account, and why V2 is still weaker'
\echo '-- than V1.'
WITH t AS (SELECT reference, count(*) AS n FROM fee GROUP BY 1)
SELECT count(*) AS distinct_references, sum(n) AS approved_fee_journals,
       max(n) AS most_journals_on_one_reference, round(avg(n),1) AS mean_per_reference
FROM t;

ROLLBACK;
