-- ===========================================================================
-- Stranded-repost detector — the ledger-decidable proofs, as set logic.
--
-- WHAT THIS SETTLES
--
-- `docs/inventories/accounting-finance-correction-memorandum.md` §3–§4 counts
-- 2,039 APPROVED `AR`/`INVOICE` journals and is explicit that they are
-- CANDIDATES identified by producer, not a proven cohort. Nothing may be called
-- stranded, and no VAT claim may be made about any of them, until each passes:
--
--     original -> reversal -> orphan -> no later replacement
--
-- A prior investigation proved 2,038. The one-row delta against these 2,039 is
-- unexplained, and the memorandum requires it be resolved BY RUNNING THIS, not
-- by assuming the two counts describe the same set.
--
-- WHY IT LOOKS LIKE THIS
--
-- The earlier attempt used correlated per-invoice subqueries. Against the
-- standby PostgreSQL cancelled it — `canceling statement due to conflict with
-- recovery` — because a long-lived snapshot conflicts with replay. That is
-- expected behaviour for a long query on a hot standby, not a fault to be
-- worked around by raising `max_standby_streaming_delay` or by retrying.
--
-- So every proof here is SET-BASED: two aggregate passes and equi-joins. A
-- journal's economic effect is reduced to one signature string — `account:net`
-- per account, ordered by account, `trim_scale`d so `100.000000` and `100`
-- compare equal — which turns "did the reversal eliminate the original?" and
-- "does the orphan restore it?" into comparisons the planner can hash.
--
-- WHAT IT CANNOT DECIDE
--
-- * Proof 6 of memorandum §4 — customer balance, tax treatment, currency and
--   period still correct — is NOT decidable from the ledger. It is a Finance
--   determination. A candidate this script calls proven has NOT discharged it.
-- * A replacement posted as a manual journal carrying no `source_document_id`
--   cannot be linked to its invoice by any ledger query, so proof 5 cannot see
--   it. Proof 5 is therefore necessary, not sufficient.
--
-- READ-ONLY. Creates temp tables in its own session and writes nothing. Run
-- against an ISOLATED RESTORED DATABASE, per the memorandum.
-- ===========================================================================

\set ON_ERROR_STOP on
\timing on
\pset pager off

-- Everything runs inside one transaction that ends in ROLLBACK. Temp tables are
-- the only things created and nothing else is written; the transaction is not
-- declared READ ONLY only because that mode disallows CREATE TEMP TABLE.
BEGIN;

-- --------------------------------------------------------------------------
-- Net effect per (journal, account), in functional currency — the currency the
-- ledger balances in.
-- --------------------------------------------------------------------------
CREATE TEMP TABLE je_net AS
SELECT jel.journal_entry_id,
       jel.account_id,
       sum(jel.debit_amount_functional - jel.credit_amount_functional) AS net
FROM gl.journal_entry_line jel
GROUP BY 1, 2;

CREATE INDEX ON je_net (journal_entry_id);

-- `fwd` is the journal's effect; `neg` is the effect that would exactly cancel
-- it. Zero-net accounts are dropped: an account touched on both sides for the
-- same amount is not part of the economic effect, and keeping it would make two
-- economically identical journals compare unequal.
CREATE TEMP TABLE je_sig AS
SELECT journal_entry_id,
       string_agg(account_id::text || ':' || trim_scale(net)::text,
                  ',' ORDER BY account_id) AS fwd,
       string_agg(account_id::text || ':' || trim_scale(-net)::text,
                  ',' ORDER BY account_id) AS neg
FROM je_net
WHERE net <> 0
GROUP BY 1;

CREATE UNIQUE INDEX ON je_sig (journal_entry_id);

-- --------------------------------------------------------------------------
-- Candidates: the population memorandum §3 counted.
-- --------------------------------------------------------------------------
CREATE TEMP TABLE candidate AS
SELECT je.journal_entry_id,
       je.journal_number,
       je.source_document_id AS invoice_id,
       je.entry_date,
       je.posting_date,
       je.total_debit_functional,
       inv.invoice_type
FROM gl.journal_entry je
LEFT JOIN ar.invoice inv ON inv.invoice_id = je.source_document_id
WHERE je.status = 'APPROVED'
  AND je.source_module = 'AR'
  AND je.source_document_type = 'INVOICE';

CREATE INDEX ON candidate (invoice_id);

\echo ''
\echo '===== 0. Candidate population (memorandum §3 expects 2,039) ====='
SELECT count(*)                                   AS candidates,
       count(DISTINCT invoice_id)                 AS distinct_invoices,
       count(*) FILTER (WHERE invoice_id IS NULL) AS no_source_document,
       sum(total_debit_functional)                AS gross_debit
FROM candidate;

-- --------------------------------------------------------------------------
-- Every journal touching a candidate's invoice. Deliberately NOT filtered by
-- source_document_type: a replacement tagged differently still counts against
-- proof 5, and restricting the type here would manufacture a pass.
-- --------------------------------------------------------------------------
CREATE TEMP TABLE invoice_journal AS
SELECT je.journal_entry_id,
       je.source_document_id AS invoice_id,
       je.status,
       je.is_reversal,
       je.reversed_journal_id,
       je.posting_date
FROM gl.journal_entry je
JOIN (SELECT DISTINCT invoice_id FROM candidate WHERE invoice_id IS NOT NULL) c
  ON c.invoice_id = je.source_document_id;

CREATE INDEX ON invoice_journal (invoice_id);
CREATE INDEX ON invoice_journal (reversed_journal_id);

-- --------------------------------------------------------------------------
-- The chain: original P, its reversal R, the orphan O.
--
-- P is reached FROM the reversal via `reversed_journal_id`, and NO status
-- filter is applied to P. Both points matter:
--
-- * `reversal_journal_id` on P is unpopulated for this cohort while
--   `reversed_journal_id` on R is populated for all of it, so following the
--   link the other way finds nothing.
-- * A reversed original does not stay POSTED — ERP moves it to `REVERSED`.
--   An earlier version of this detector required `p.status = 'POSTED'` and so
--   reported ZERO chains across all 2,039 candidates. That result was a defect
--   in the query, not a finding about the data, and it is recorded here so the
--   filter is not reintroduced. P's status is now carried through as an
--   observable (`original_status`) instead of being assumed.
-- --------------------------------------------------------------------------
CREATE TEMP TABLE chain AS
SELECT o.journal_entry_id       AS orphan_id,
       o.journal_number         AS orphan_number,
       o.invoice_id,
       o.posting_date           AS orphan_posting_date,
       o.total_debit_functional AS orphan_debit,
       o.invoice_type,
       p.journal_entry_id       AS original_id,
       p.status                 AS original_status,
       r.journal_entry_id       AS reversal_id,
       r.posting_date           AS reversal_posting_date
FROM candidate o
JOIN invoice_journal r
  ON r.invoice_id = o.invoice_id
 AND r.is_reversal
 AND r.status = 'POSTED'
JOIN invoice_journal p
  ON p.journal_entry_id = r.reversed_journal_id
 AND p.journal_entry_id <> o.journal_entry_id;

CREATE INDEX ON chain (orphan_id);

\echo ''
\echo '===== 1. Candidates that have an original+reversal pair at all ====='
SELECT (SELECT count(*) FROM candidate)                     AS candidates,
       count(DISTINCT orphan_id)                            AS with_a_chain,
       (SELECT count(*) FROM candidate)
         - count(DISTINCT orphan_id)                        AS without_a_chain,
       count(*)                                             AS chain_rows,
       count(*) - count(DISTINCT orphan_id)                 AS ambiguous_extra_rows
FROM chain;

-- --------------------------------------------------------------------------
-- The proofs, one row per chain.
-- --------------------------------------------------------------------------
CREATE TEMP TABLE proof AS
SELECT c.orphan_id,
       c.orphan_number,
       c.invoice_id,
       c.original_id,
       c.reversal_id,
       c.orphan_debit,
       c.orphan_posting_date,
       c.invoice_type,

       -- 1. the underlying invoice remains valid
       (inv.invoice_id IS NOT NULL
        AND inv.status NOT IN ('VOID', 'VOIDED', 'CANCELLED'))     AS p1_invoice_valid,

       -- 2. the original was reversed (established by the chain join)
       TRUE                                                        AS p2_original_reversed,

       -- 3. the reversal eliminated the original effect, account for account
       (rs.fwd IS NOT NULL AND ps.neg IS NOT NULL
        AND rs.fwd = ps.neg)                                       AS p3_reversal_exact,

       -- 4. the orphan is exactly the replacement the original was
       (os.fwd IS NOT NULL AND ps.fwd IS NOT NULL
        AND os.fwd = ps.fwd)                                       AS p4_orphan_matches,

       -- 5. nothing already restored the effect at or after the reversal
       NOT EXISTS (
           SELECT 1
           FROM invoice_journal later
           JOIN je_sig ls ON ls.journal_entry_id = later.journal_entry_id
           WHERE later.invoice_id = c.invoice_id
             AND later.status = 'POSTED'
             AND later.journal_entry_id <> c.original_id
             AND later.journal_entry_id <> c.reversal_id
             AND later.posting_date >= c.reversal_posting_date
             AND ls.fwd = ps.fwd
       )                                                           AS p5_not_already_restored,

       c.original_status,
       inv.status                                                  AS invoice_status,
       inv.invoice_number,
       ps.fwd                                                      AS original_signature
FROM chain c
LEFT JOIN ar.invoice inv ON inv.invoice_id = c.invoice_id
LEFT JOIN je_sig ps ON ps.journal_entry_id = c.original_id
LEFT JOIN je_sig rs ON rs.journal_entry_id = c.reversal_id
LEFT JOIN je_sig os ON os.journal_entry_id = c.orphan_id;

CREATE TEMP TABLE verdict AS
SELECT *,
       (p1_invoice_valid AND p2_original_reversed AND p3_reversal_exact
        AND p4_orphan_matches AND p5_not_already_restored) AS proven
FROM proof;

\echo ''
\echo '===== 1b. Status of the original each reversal points at ====='
SELECT original_status, count(*) AS chains FROM chain GROUP BY 1 ORDER BY 2 DESC;

\echo ''
\echo '===== 1c. Invoice status across candidate invoices ====='
SELECT COALESCE(inv.status, '(no invoice row)') AS invoice_status, count(*) AS invoices
FROM (SELECT DISTINCT invoice_id FROM candidate) c
LEFT JOIN ar.invoice inv ON inv.invoice_id = c.invoice_id
GROUP BY 1 ORDER BY 2 DESC;

\echo ''
\echo '===== 2. Each proof independently ====='
SELECT count(*)                                        AS chain_rows,
       count(*) FILTER (WHERE p1_invoice_valid)        AS pass_1_invoice_valid,
       count(*) FILTER (WHERE p2_original_reversed)    AS pass_2_original_reversed,
       count(*) FILTER (WHERE p3_reversal_exact)       AS pass_3_reversal_exact,
       count(*) FILTER (WHERE p4_orphan_matches)       AS pass_4_orphan_matches,
       count(*) FILTER (WHERE p5_not_already_restored) AS pass_5_not_already_restored
FROM verdict;

\echo ''
\echo '===== 3. THE ANSWER: candidates passing every ledger-decidable proof ====='
SELECT count(DISTINCT orphan_id) AS proven_stranded,
       sum(orphan_debit)         AS gross_debit
FROM verdict
WHERE proven;

\echo ''
\echo '===== 4. Disposition of ALL 2,039 candidates ====='
WITH d AS (
  SELECT c.journal_entry_id,
         c.total_debit_functional,
         CASE
           WHEN v.orphan_id IS NULL              THEN 'E no original+reversal chain'
           WHEN v.proven                          THEN 'A proven stranded'
           WHEN NOT v.p1_invoice_valid            THEN 'B invoice not valid'
           WHEN NOT v.p3_reversal_exact           THEN 'C reversal did not exactly eliminate the original'
           WHEN NOT v.p4_orphan_matches           THEN 'D orphan is not the original replacement'
           WHEN NOT v.p5_not_already_restored     THEN 'F effect already restored by a later journal'
         END AS disposition
  FROM candidate c
  LEFT JOIN verdict v ON v.orphan_id = c.journal_entry_id
)
SELECT disposition,
       count(*)                     AS candidates,
       sum(total_debit_functional)  AS gross_debit
FROM d GROUP BY 1
UNION ALL
SELECT 'TOTAL', count(*), sum(total_debit_functional) FROM candidate
ORDER BY 1;

\echo ''
\echo '===== 4b. Disposition by document type — THE population is credit notes ====='
\echo '-- Memorandum §4 frames this cohort as invoices whose GL effect is missing.'
\echo '-- Restoring a CREDIT NOTE reduces receivables and revenue; it does not'
\echo '-- restore missing income. The two are opposite economic acts.'
SELECT COALESCE(c.invoice_type, '(no invoice row)') AS invoice_type,
       CASE WHEN v.orphan_id IS NULL THEN 'no chain'
            WHEN v.proven            THEN 'proven stranded'
            ELSE 'not proven' END                   AS disposition,
       count(*)                                     AS candidates,
       sum(c.total_debit_functional)                AS gross_debit
FROM candidate c LEFT JOIN verdict v ON v.orphan_id = c.journal_entry_id
GROUP BY 1,2 ORDER BY 3 DESC;

\echo ''
\echo '===== 5. The 2,039-vs-2,038 delta ====='
SELECT (SELECT count(*) FROM candidate)                                  AS candidates_by_producer,
       (SELECT count(DISTINCT orphan_id) FROM verdict WHERE proven)      AS proven_by_detector,
       (SELECT count(*) FROM candidate)
         - (SELECT count(DISTINCT orphan_id) FROM verdict WHERE proven)  AS delta;

\echo ''
\echo '===== 6. Proven cohort by posting period (Finance / tax review) ====='
SELECT to_char(orphan_posting_date, 'YYYY-MM')  AS period,
       COALESCE(invoice_type, '(none)')         AS invoice_type,
       count(*)                                 AS journals,
       sum(orphan_debit)                        AS gross_debit
FROM verdict WHERE proven
GROUP BY 1, 2 ORDER BY 1, 2;

\echo ''
\echo '===== 7. Proven cohort net effect by account (Finance / tax review) ====='
\echo '-- No VAT account appears. Memorandum §3 required that be TESTED before any'
\echo '-- claim of a missing VAT effect; this is the test, and it is negative.'
SELECT a.account_code,
       a.account_name,
       a.account_type,
       count(DISTINCT v.orphan_id)  AS journals,
       sum(n.net)                   AS net_debit_effect
FROM verdict v
JOIN je_net n ON n.journal_entry_id = v.orphan_id
JOIN gl.account a ON a.account_id = n.account_id
WHERE v.proven AND n.net <> 0
GROUP BY 1,2,3
ORDER BY abs(sum(n.net)) DESC;

\echo ''
\echo '===== 8. Non-proven candidates, by disposition and period ====='
SELECT to_char(v.orphan_posting_date, 'YYYY-MM') AS period,
       CASE
         WHEN NOT v.p1_invoice_valid        THEN 'B invoice not valid'
         WHEN NOT v.p3_reversal_exact       THEN 'C reversal inexact'
         WHEN NOT v.p4_orphan_matches       THEN 'D orphan mismatch'
         WHEN NOT v.p5_not_already_restored THEN 'F already restored'
       END                                       AS disposition,
       count(*)                                  AS journals,
       sum(v.orphan_debit)                       AS gross_debit
FROM verdict v WHERE NOT v.proven
GROUP BY 1,2 ORDER BY 1,2;

\echo ''
\echo '===== 9. Every non-proven candidate, identified ====='
SELECT orphan_number, invoice_number, invoice_status, orphan_debit,
       p1_invoice_valid, p3_reversal_exact, p4_orphan_matches,
       p5_not_already_restored
FROM verdict WHERE NOT proven
ORDER BY orphan_debit DESC NULLS LAST;

\echo ''
\echo '===== 10. Candidates with no chain, identified ====='
SELECT c.journal_number, c.posting_date, c.total_debit_functional
FROM candidate c
WHERE NOT EXISTS (SELECT 1 FROM chain ch WHERE ch.orphan_id = c.journal_entry_id)
ORDER BY c.total_debit_functional DESC NULLS LAST;

\echo ''
\echo '===== 11. Every account any candidate touches (unfiltered — nothing hides) ====='
\echo '-- Section 7 drops accounts whose net is zero. This one drops nothing, so an'
\echo '-- account touched equally on both sides still appears.'
SELECT a.account_code, a.account_name,
       count(DISTINCT l.journal_entry_id) AS journals,
       sum(l.debit_amount_functional)     AS total_debit,
       sum(l.credit_amount_functional)    AS total_credit
FROM candidate c
JOIN gl.journal_entry_line l ON l.journal_entry_id = c.journal_entry_id
JOIN gl.account a ON a.account_id = l.account_id
GROUP BY 1, 2 ORDER BY 3 DESC;

ROLLBACK;
