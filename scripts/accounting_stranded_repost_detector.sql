-- ===========================================================================
-- Stranded-repost detector for the ERP APPROVED `AR`/`INVOICE` cohort.
--
-- Read-only: one transaction, temp tables only, ending in ROLLBACK. Run against
-- an ISOLATED RESTORED DATABASE, never a production instance.
--
-- WHAT IT DECIDES, AND IN WHAT WORDS
--
-- This script does NOT prove that an orphan journal is an "exact replacement"
-- for its original. It proves one thing, and the wording matters because the
-- difference is where a wrong disposition would come from:
--
--     the orphan has the SAME FUNCTIONAL-CURRENCY NET EFFECT BY ACCOUNT
--     as the original
--
-- It says nothing about customer, cost centre, project or segment dimensions,
-- transaction currency, exchange rate, tax treatment, or line-level structure.
-- Two journals with different customers, different tax codes and a different
-- line breakdown can share a signature.
--
-- That is not hypothetical in this ledger. Section 12 measures it: 205,285
-- journals carry only 13,566 distinct signatures, 96% of journals share theirs
-- with at least one other, and the most-shared signature covers 15,887
-- journals. Identical net effect is the NORM here. A signature match between
-- two DIFFERENT documents is therefore worth nothing on its own — which is why
-- proofs 3 and 4 only ever compare journals on the SAME document.
--
-- WHY EVERY PROOF IS SET-BASED
--
-- The first attempt used correlated per-invoice subqueries and PostgreSQL
-- cancelled it on the standby — `canceling statement due to conflict with
-- recovery`. That is expected behaviour for a long-lived snapshot during
-- replay, not a fault to work around by raising `max_standby_streaming_delay`
-- or by retrying. Reducing each journal to one signature string makes the whole
-- thing hash joins.
--
-- WHAT IT CANNOT DECIDE
--
-- * Proof 6 of memorandum §4 — source validity, customer subledger effect, GL
--   control effect, tax treatment, currency, period — is not decidable from
--   these tables. It is a per-document Finance reconciliation.
-- * A replacement posted with NO `source_document_id` cannot be linked to its
--   invoice by any ledger query. Section 11 searches for such journals by
--   effect, but section 12 shows why that search returns candidates rather than
--   answers.
-- * Every result is as of the copy it runs against. A disposition must be
--   revalidated against current authoritative state immediately before
--   execution.
-- ===========================================================================

\set ON_ERROR_STOP on
\timing on
\pset pager off

-- The tenant this run is scoped to. Dotmac Technologies Ltd is the only
-- organization in this deployment TODAY, which is exactly why the predicate has
-- to be explicit and tested — a query that is accidentally correct because
-- there is one tenant silently becomes wrong when there are two. Section 0b is
-- the sensitivity proof that the predicate bites.
\set ORG '00000000-0000-0000-0000-000000000001'

BEGIN;

CREATE TEMP TABLE je_net AS
SELECT jel.journal_entry_id, jel.account_id,
       sum(jel.debit_amount_functional - jel.credit_amount_functional) AS net
FROM gl.journal_entry_line jel
GROUP BY 1, 2;
CREATE INDEX ON je_net (journal_entry_id);

-- `fwd` is the journal's net effect by account; `neg` is what would cancel it.
-- Zero-net accounts are dropped so two economically identical journals compare
-- equal. See the header: this is a NET EFFECT signature, not an identity.
CREATE TEMP TABLE je_sig AS
SELECT journal_entry_id,
       string_agg(account_id::text || ':' || trim_scale(net)::text, ',' ORDER BY account_id) AS fwd,
       string_agg(account_id::text || ':' || trim_scale(-net)::text, ',' ORDER BY account_id) AS neg
FROM je_net WHERE net <> 0 GROUP BY 1;
CREATE UNIQUE INDEX ON je_sig (journal_entry_id);
CREATE INDEX ON je_sig (fwd);

CREATE TEMP TABLE candidate AS
SELECT je.journal_entry_id, je.journal_number, je.source_document_id AS invoice_id,
       je.entry_date, je.posting_date, je.total_debit_functional,
       inv.invoice_type, inv.currency_code
FROM gl.journal_entry je
LEFT JOIN ar.invoice inv
       ON inv.invoice_id = je.source_document_id
      AND inv.organization_id = je.organization_id
WHERE je.organization_id = :'ORG'::uuid
  AND je.status = 'APPROVED'
  AND je.source_module = 'AR'
  AND je.source_document_type = 'INVOICE';
CREATE INDEX ON candidate (invoice_id);

\echo ''
\echo '===== 0. Candidate population, scoped to one organization ====='
SELECT count(*) AS candidates, count(DISTINCT invoice_id) AS distinct_invoices,
       count(*) FILTER (WHERE invoice_id IS NULL) AS no_source_document,
       count(DISTINCT currency_code) AS transaction_currencies,
       sum(total_debit_functional) AS gross_debit
FROM candidate;

\echo ''
\echo '===== 0b. SENSITIVITY PROOF: the organization predicate actually bites ====='
\echo '-- Inserts one synthetic second-tenant journal that would otherwise qualify,'
\echo '-- then shows the scoped count ignores it and the unscoped count does not.'
\echo '-- Written inside the transaction this script rolls back; nothing persists.'
INSERT INTO gl.journal_entry
  (journal_entry_id, organization_id, journal_number, entry_date, posting_date,
   status, is_reversal, source_module, source_document_type, source_document_id,
   total_debit_functional)
VALUES
  ('00000000-0000-0000-0000-0000000000ff'::uuid,
   '00000000-0000-0000-0000-0000000000aa'::uuid,
   'SENSITIVITY-CANARY', DATE '2026-01-01', DATE '2026-01-01',
   'APPROVED', false, 'AR', 'INVOICE',
   '00000000-0000-0000-0000-0000000000fe'::uuid, 1.000000);

SELECT
  (SELECT count(*) FROM gl.journal_entry
    WHERE status='APPROVED' AND source_module='AR' AND source_document_type='INVOICE')
      AS unscoped_now_includes_the_canary,
  (SELECT count(*) FROM gl.journal_entry
    WHERE organization_id = :'ORG'::uuid
      AND status='APPROVED' AND source_module='AR' AND source_document_type='INVOICE')
      AS scoped_excludes_the_canary,
  (SELECT count(*) FROM candidate) AS detector_population;

\echo '-- The first number MUST exceed the other two by exactly 1. If it does not,'
\echo '-- the predicate is not doing anything and every count below is unscoped.'
DELETE FROM gl.journal_entry WHERE journal_number = 'SENSITIVITY-CANARY';

CREATE TEMP TABLE invoice_journal AS
SELECT je.journal_entry_id, je.source_document_id AS invoice_id, je.status,
       je.is_reversal, je.reversed_journal_id, je.posting_date
FROM gl.journal_entry je
JOIN (SELECT DISTINCT invoice_id FROM candidate WHERE invoice_id IS NOT NULL) c
  ON c.invoice_id = je.source_document_id
WHERE je.organization_id = :'ORG'::uuid;
CREATE INDEX ON invoice_journal (invoice_id);
CREATE INDEX ON invoice_journal (reversed_journal_id);

-- The chain: original P, its reversal R, the orphan O.
--
-- P is reached FROM the reversal via `reversed_journal_id`, with NO status
-- filter. Both points matter. `reversal_journal_id` on P is unpopulated for
-- this cohort while `reversed_journal_id` on R is populated throughout, so
-- following the link the other way finds nothing. And a reversed original does
-- not stay POSTED — ERP moves it to `REVERSED`. An earlier version required
-- `p.status = 'POSTED'` and reported ZERO chains across all 2,039 candidates;
-- that was a defect in the query, not a finding about the data. P's status is
-- carried through as an observable instead of being assumed.
CREATE TEMP TABLE chain AS
SELECT o.journal_entry_id AS orphan_id, o.journal_number AS orphan_number,
       o.invoice_id, o.posting_date AS orphan_posting_date,
       o.total_debit_functional AS orphan_debit, o.invoice_type, o.currency_code,
       p.journal_entry_id AS original_id, p.status AS original_status,
       r.journal_entry_id AS reversal_id, r.posting_date AS reversal_posting_date
FROM candidate o
JOIN invoice_journal r ON r.invoice_id = o.invoice_id
                      AND r.is_reversal AND r.status = 'POSTED'
JOIN invoice_journal p ON p.journal_entry_id = r.reversed_journal_id
                      AND p.journal_entry_id <> o.journal_entry_id;
CREATE INDEX ON chain (orphan_id);

\echo ''
\echo '===== 1. Candidates with an original+reversal pair ====='
SELECT (SELECT count(*) FROM candidate) AS candidates,
       count(DISTINCT orphan_id) AS with_a_chain,
       (SELECT count(*) FROM candidate) - count(DISTINCT orphan_id) AS without_a_chain,
       count(*) AS chain_rows,
       count(*) - count(DISTINCT orphan_id) AS ambiguous_extra_rows
FROM chain;

\echo ''
\echo '===== 1b. Status of the original each reversal points at ====='
SELECT original_status, count(*) AS chains FROM chain GROUP BY 1 ORDER BY 2 DESC;

\echo ''
\echo '===== 1c. Invoice status across candidate invoices ====='
SELECT COALESCE(inv.status, '(no invoice row)') AS invoice_status, count(*) AS invoices
FROM (SELECT DISTINCT invoice_id FROM candidate) c
LEFT JOIN ar.invoice inv ON inv.invoice_id = c.invoice_id
GROUP BY 1 ORDER BY 2 DESC;

CREATE TEMP TABLE verdict AS
SELECT c.orphan_id, c.orphan_number, c.invoice_id, c.original_id, c.reversal_id,
       c.orphan_debit, c.orphan_posting_date, c.invoice_type, c.currency_code,

       -- 1. the underlying invoice is present and not void or cancelled
       (inv.invoice_id IS NOT NULL
        AND inv.status NOT IN ('VOID','VOIDED','CANCELLED'))          AS p1_invoice_not_void,

       -- 2. the original was reversed (established by the chain join)
       TRUE                                                           AS p2_original_reversed,

       -- 3. the reversal's net effect by account is the exact negation of the
       --    original's. Same document, so signature collision is not a risk.
       (rs.fwd IS NOT NULL AND ps.neg IS NOT NULL AND rs.fwd = ps.neg) AS p3_reversal_negates_original,

       -- 4. the orphan has the SAME NET EFFECT BY ACCOUNT as the original.
       --    NOT "is an exact replacement": see the header.
       (os.fwd IS NOT NULL AND ps.fwd IS NOT NULL AND os.fwd = ps.fwd) AS p4_same_net_effect_as_original,

       -- 5a. no currently-POSTED journal on this document carries the original's
       --     effect — ANY posting date, not just after the reversal.
       NOT EXISTS (
           SELECT 1 FROM invoice_journal j JOIN je_sig js ON js.journal_entry_id = j.journal_entry_id
           WHERE j.invoice_id = c.invoice_id AND j.status = 'POSTED'
             AND j.journal_entry_id <> c.original_id
             AND j.journal_entry_id <> c.reversal_id
             AND js.fwd = ps.fwd
       )                                                              AS p5a_no_posted_equivalent,

       -- 5b. stricter still: no currently-POSTED journal on this document at
       --     all beyond the reversal, whatever its effect.
       NOT EXISTS (
           SELECT 1 FROM invoice_journal j
           WHERE j.invoice_id = c.invoice_id AND j.status = 'POSTED'
             AND j.journal_entry_id <> c.reversal_id
       )                                                              AS p5b_no_other_posted_journal,

       inv.status AS invoice_status, inv.invoice_number
FROM chain c
LEFT JOIN ar.invoice inv ON inv.invoice_id = c.invoice_id
LEFT JOIN je_sig ps ON ps.journal_entry_id = c.original_id
LEFT JOIN je_sig rs ON rs.journal_entry_id = c.reversal_id
LEFT JOIN je_sig os ON os.journal_entry_id = c.orphan_id;

ALTER TABLE verdict ADD COLUMN ledger_chain_proven boolean;
UPDATE verdict SET ledger_chain_proven =
  (p1_invoice_not_void AND p2_original_reversed AND p3_reversal_negates_original
   AND p4_same_net_effect_as_original AND p5a_no_posted_equivalent
   AND p5b_no_other_posted_journal);

\echo ''
\echo '===== 2. Each proof independently ====='
SELECT count(*) AS chain_rows,
       count(*) FILTER (WHERE p1_invoice_not_void)            AS pass_1_invoice_not_void,
       count(*) FILTER (WHERE p2_original_reversed)           AS pass_2_original_reversed,
       count(*) FILTER (WHERE p3_reversal_negates_original)   AS pass_3_reversal_negates,
       count(*) FILTER (WHERE p4_same_net_effect_as_original) AS pass_4_same_net_effect,
       count(*) FILTER (WHERE p5a_no_posted_equivalent)       AS pass_5a_no_posted_equivalent,
       count(*) FILTER (WHERE p5b_no_other_posted_journal)    AS pass_5b_no_other_posted
FROM verdict;

\echo ''
\echo '===== 3. Candidates whose LEDGER CHAIN is proven ====='
\echo '-- NOT an approval to post. Proof 6 of memorandum §4 is a per-document'
\echo '-- Finance reconciliation and is not decidable here.'
SELECT count(DISTINCT orphan_id) AS ledger_chain_proven, sum(orphan_debit) AS gross_debit
FROM verdict WHERE ledger_chain_proven;

\echo ''
\echo '===== 4. Disposition of every candidate ====='
WITH d AS (
  SELECT c.journal_entry_id, c.total_debit_functional, c.invoice_type,
         CASE WHEN v.orphan_id IS NULL                     THEN 'E no original+reversal chain'
              WHEN v.ledger_chain_proven                   THEN 'A ledger chain proven'
              WHEN NOT v.p1_invoice_not_void               THEN 'B invoice void or cancelled'
              WHEN NOT v.p3_reversal_negates_original      THEN 'C reversal does not negate the original'
              WHEN NOT v.p4_same_net_effect_as_original    THEN 'D orphan net effect differs from the original'
              ELSE 'F an equivalent is already posted' END AS disposition
  FROM candidate c LEFT JOIN verdict v ON v.orphan_id = c.journal_entry_id
)
SELECT disposition, count(*) AS candidates, sum(total_debit_functional) AS gross_debit
FROM d GROUP BY 1
UNION ALL SELECT 'TOTAL', count(*), sum(total_debit_functional) FROM candidate
ORDER BY 1;

\echo ''
\echo '===== 5. THE FOUR COHORTS — these are separate decisions ====='
\echo '-- Credit notes REDUCE receivables and revenue; standard invoices increase'
\echo '-- them. A single net batch would hide two opposite decisions.'
WITH d AS (
  SELECT c.journal_entry_id, c.total_debit_functional, c.invoice_type,
         CASE WHEN v.orphan_id IS NULL                  THEN 'no chain'
              WHEN v.ledger_chain_proven                THEN 'chain proven'
              ELSE 'not proven' END                     AS state
  FROM candidate c LEFT JOIN verdict v ON v.orphan_id = c.journal_entry_id
)
SELECT COALESCE(invoice_type,'(no invoice row)') AS invoice_type, state,
       count(*) AS candidates, sum(total_debit_functional) AS gross_debit
FROM d GROUP BY 1,2 ORDER BY 3 DESC;

\echo ''
\echo '===== 6. Chain-proven cohort by posting period and type ====='
\echo '-- These are 2026 postings. Correction belongs in the ORIGINATING period,'
\echo '-- not as a single later net adjustment (IAS 8).'
SELECT to_char(orphan_posting_date,'YYYY-MM') AS period,
       COALESCE(invoice_type,'(none)') AS invoice_type,
       count(*) AS journals, sum(orphan_debit) AS gross_debit
FROM verdict WHERE ledger_chain_proven GROUP BY 1,2 ORDER BY 1,2;

\echo ''
\echo '===== 7. Chain-proven net effect by account and type ====='
\echo '-- Split by type deliberately. The netted single figure is not a decision.'
SELECT COALESCE(v.invoice_type,'(none)') AS invoice_type,
       a.account_code, a.account_name,
       count(DISTINCT v.orphan_id) AS journals, sum(n.net) AS net_debit_effect
FROM verdict v
JOIN je_net n ON n.journal_entry_id = v.orphan_id
JOIN gl.account a ON a.account_id = n.account_id
WHERE v.ledger_chain_proven AND n.net <> 0
GROUP BY 1,2,3 ORDER BY 1, abs(sum(n.net)) DESC;

\echo ''
\echo '===== 8. Non-proven candidates, by disposition and period ====='
SELECT to_char(orphan_posting_date,'YYYY-MM') AS period,
       CASE WHEN NOT p1_invoice_not_void            THEN 'B invoice void or cancelled'
            WHEN NOT p3_reversal_negates_original   THEN 'C reversal does not negate'
            WHEN NOT p4_same_net_effect_as_original THEN 'D net effect differs'
            ELSE 'F equivalent already posted' END  AS disposition,
       count(*) AS journals, sum(orphan_debit) AS gross_debit
FROM verdict WHERE NOT ledger_chain_proven GROUP BY 1,2 ORDER BY 1,2;

\echo ''
\echo '===== 9. Every non-proven candidate, identified ====='
SELECT orphan_number, invoice_number, invoice_type, invoice_status, orphan_debit,
       p1_invoice_not_void, p3_reversal_negates_original,
       p4_same_net_effect_as_original, p5a_no_posted_equivalent, p5b_no_other_posted_journal
FROM verdict WHERE NOT ledger_chain_proven ORDER BY orphan_debit DESC NULLS LAST;

\echo ''
\echo '===== 10. Candidates with no chain, identified ====='
SELECT c.journal_number, c.posting_date, c.invoice_type, c.total_debit_functional
FROM candidate c
WHERE NOT EXISTS (SELECT 1 FROM chain ch WHERE ch.orphan_id = c.journal_entry_id)
ORDER BY c.total_debit_functional DESC NULLS LAST;

\echo ''
\echo '===== 11. Possible untagged replacements: POSTED, no source_document_id ====='
\echo '-- These CANNOT be linked to an invoice by any ledger query. Read section 12'
\echo '-- before treating any of them as a replacement.'
WITH targets AS (SELECT DISTINCT ps.fwd FROM chain c JOIN je_sig ps ON ps.journal_entry_id = c.original_id)
SELECT j.journal_number, j.source_module, j.source_document_type, j.posting_date,
       j.total_debit_functional
FROM gl.journal_entry j
JOIN je_sig js ON js.journal_entry_id = j.journal_entry_id
JOIN targets t ON t.fwd = js.fwd
WHERE j.organization_id = :'ORG'::uuid
  AND j.status = 'POSTED' AND j.source_document_id IS NULL
ORDER BY j.total_debit_functional DESC;

\echo ''
\echo '===== 12. SIGNATURE COLLISION — why section 11 lists candidates, not answers ====='
\echo '-- If an identical net effect is common, a signature match between two'
\echo '-- DIFFERENT documents is worth nothing as evidence of replacement.'
WITH t AS (SELECT fwd, count(*) AS journals FROM je_sig GROUP BY 1)
SELECT count(*) AS distinct_signatures, sum(journals) AS journals_with_a_signature,
       count(*) FILTER (WHERE journals > 1) AS signatures_shared_by_2plus,
       sum(journals) FILTER (WHERE journals > 1) AS journals_sharing_a_signature,
       max(journals) AS most_journals_on_one_signature
FROM t;

\echo '-- …and among the candidate originals themselves:'
WITH t AS (SELECT ps.fwd, count(*) AS n FROM chain c JOIN je_sig ps ON ps.journal_entry_id = c.original_id GROUP BY 1)
SELECT count(*) AS distinct_original_signatures, sum(n) AS originals, max(n) AS most_on_one_signature FROM t;

ROLLBACK;
