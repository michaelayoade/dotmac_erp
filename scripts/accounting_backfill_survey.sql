-- Accounting backfill survey — READ ONLY. Gate D0.
--
-- Answers the questions that decide the shape and cost of the Accounting
-- backfill, before any code is written against production shapes.
--
-- SAFETY: every statement is a SELECT. There is no INSERT, UPDATE, DELETE,
-- TRUNCATE, ALTER, CREATE or DROP in this file, and it opens no transaction
-- that writes. It is safe to run against a live database, and it is the whole
-- reason the measurement is SQL rather than the ORM extractor: nothing here can
-- take a lock that matters or write a row.
--
-- WHERE TO RUN IT: a hot standby, not the primary. `dotmac_erp_standby` on
-- `db-primary` is a replica in recovery, so PostgreSQL itself refuses writes
-- there. That is a stronger guarantee than "every statement in this file is a
-- SELECT" — it does not depend on this file staying correct, or on the operator
-- reading it. Prefer it even though the queries are read-only anyway.
--
--   ssh db-primary 'docker exec -i dotmac_erp_standby \
--     psql -U postgres -d dotmac_erp -v ON_ERROR_STOP=0' \
--     < scripts/accounting_backfill_survey.sql
--
-- ON_ERROR_STOP=0 so one failing section does not abort the other eleven.
--
-- WHEN TO RUN IT: before the Accounting backfill is designed (done —
-- `docs/inventories/accounting-backfill-survey-2026-08-21.md` records the
-- answers), and again immediately before cutover, to prove nothing moved.
-- A survey whose result is a year old is a guess.
--
-- Every result set is labelled. Read them in order; sections 2 and 3 are the
-- ones that can stop Gate D.

\echo '===== 0. Organizations (expect exactly one: Dotmac Technologies) ====='
SELECT organization_id,
       organization_code,
       legal_name,
       functional_currency_code,
       presentation_currency_code
FROM core_org.organization
ORDER BY organization_code;

\echo '===== 1. Volumes ====='
SELECT 'account_categories' AS relation, count(*) FROM gl.account_category
UNION ALL SELECT 'accounts',            count(*) FROM gl.account
UNION ALL SELECT 'fiscal_years',        count(*) FROM gl.fiscal_year
UNION ALL SELECT 'fiscal_periods',      count(*) FROM gl.fiscal_period
UNION ALL SELECT 'journal_entries',     count(*) FROM gl.journal_entry
UNION ALL SELECT 'journal_entry_lines', count(*) FROM gl.journal_entry_line
UNION ALL SELECT 'posted_ledger_lines', count(*) FROM gl.posted_ledger_line
UNION ALL SELECT 'posting_batches',     count(*) FROM gl.posting_batch
ORDER BY 1;

\echo '===== 2. STOPPER: journal types with no module counterpart ====='
-- The module JournalKind has FIVE values: STANDARD, ADJUSTMENT, CLOSING,
-- OPENING, REVERSAL. ERP JournalType has NINE. Any non-zero count against
-- RECURRING, INTERCOMPANY, REVALUATION or CONSOLIDATION is a decision that must
-- be made before a single journal is backfilled: map it lossily (rejected — it
-- breaks the injectivity rule the other mappings hold to), have the module gain
-- the value (a Starter release), or establish it cannot occur.
SELECT journal_type,
       status,
       count(*) AS journals
FROM gl.journal_entry
GROUP BY journal_type, status
ORDER BY journal_type, status;

\echo '===== 3. STOPPER: unbalanced journals ====='
-- The module refuses unbalanced posting. ERP's archive carries
-- fix_unbalanced_fcy_journals.sql, so this has happened before. Each row here
-- is a data decision, not a code decision.
SELECT je.journal_entry_id,
       je.journal_number,
       je.status,
       je.posting_date,
       sum(jel.debit_amount_functional)  AS debit_functional,
       sum(jel.credit_amount_functional) AS credit_functional,
       sum(jel.debit_amount_functional) - sum(jel.credit_amount_functional) AS difference
FROM gl.journal_entry je
JOIN gl.journal_entry_line jel ON jel.journal_entry_id = je.journal_entry_id
GROUP BY je.journal_entry_id, je.journal_number, je.status, je.posting_date
HAVING sum(jel.debit_amount_functional) <> sum(jel.credit_amount_functional)
ORDER BY je.posting_date
LIMIT 200;

\echo '===== 3b. Unbalanced journal COUNT (the LIMIT above hides the total) ====='
SELECT count(*) AS unbalanced_journals
FROM (
  SELECT je.journal_entry_id
  FROM gl.journal_entry je
  JOIN gl.journal_entry_line jel ON jel.journal_entry_id = je.journal_entry_id
  GROUP BY je.journal_entry_id
  HAVING sum(jel.debit_amount_functional) <> sum(jel.credit_amount_functional)
) unbalanced;

\echo '===== 4. Source provenance null rate (decides SourceIdentity) ====='
-- source_module / source_document_type / source_document_id are all NULLABLE.
-- The null rate decides whether a backfilled journal could reference its source
-- document at all. (Recommendation is to key on the GL journal regardless —
-- this measures how much provenance survives into description/reference.)
SELECT count(*)                                                   AS journals,
       count(source_module)                                       AS with_source_module,
       count(source_document_type)                                AS with_source_doc_type,
       count(source_document_id)                                  AS with_source_doc_id,
       count(*) FILTER (WHERE source_module IS NULL
                          AND source_document_type IS NULL
                          AND source_document_id IS NULL)         AS no_provenance_at_all,
       count(correlation_id)                                      AS with_correlation_id
FROM gl.journal_entry;

\echo '===== 4b. Source modules present ====='
SELECT source_module, source_document_type, count(*) AS journals
FROM gl.journal_entry
GROUP BY source_module, source_document_type
ORDER BY journals DESC
LIMIT 40;

\echo '===== 5. Reversal pairs and asymmetries ====='
-- The module links a reversal to its original (reverses_journal_id) with a
-- UNIQUE constraint allowing one reversal per journal. A dangling or one-sided
-- ERP link cannot be replayed through reverse_journal().
SELECT 'flagged is_reversal'                     AS finding, count(*)
FROM gl.journal_entry WHERE is_reversal
UNION ALL
SELECT 'has reversed_journal_id',                count(*)
FROM gl.journal_entry WHERE reversed_journal_id IS NOT NULL
UNION ALL
SELECT 'has reversal_journal_id',                count(*)
FROM gl.journal_entry WHERE reversal_journal_id IS NOT NULL
UNION ALL
SELECT 'is_reversal BUT no reversed_journal_id', count(*)
FROM gl.journal_entry WHERE is_reversal AND reversed_journal_id IS NULL
UNION ALL
SELECT 'reversed_journal_id points nowhere',     count(*)
FROM gl.journal_entry je
WHERE je.reversed_journal_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM gl.journal_entry o
                  WHERE o.journal_entry_id = je.reversed_journal_id)
UNION ALL
SELECT 'original reversed MORE THAN ONCE',       count(*)
FROM (SELECT reversed_journal_id
      FROM gl.journal_entry
      WHERE reversed_journal_id IS NOT NULL
      GROUP BY reversed_journal_id HAVING count(*) > 1) multi
ORDER BY 1;

\echo '===== 6. Multi-currency exposure ====='
SELECT count(*) FILTER (WHERE je.currency_code <> o.functional_currency_code) AS foreign_currency_journals,
       count(*)                                                              AS total_journals,
       count(DISTINCT je.currency_code)                                      AS distinct_currencies
FROM gl.journal_entry je
CROSS JOIN (SELECT functional_currency_code FROM core_org.organization LIMIT 1) o;

\echo '===== 7. Period status distribution (drives the replay) ====='
-- ERP PeriodStatus has HARD_CLOSED; the module has LOCKED. Every other value is
-- identical. The replay is: open -> post the period''s journals -> soft close ->
-- lock, to reach the status recorded here.
SELECT fy.year_code, fp.status, count(*) AS periods,
       min(fp.start_date) AS earliest, max(fp.end_date) AS latest,
       count(*) FILTER (WHERE fp.reopen_count > 0) AS reopened_at_least_once
FROM gl.fiscal_period fp
JOIN gl.fiscal_year fy ON fy.fiscal_year_id = fp.fiscal_year_id
GROUP BY fy.year_code, fp.status
ORDER BY fy.year_code, fp.status;

\echo '===== 8. Posted lines per period (sizes the shadow comparison) ====='
SELECT fy.year_code,
       fp.period_number,
       fp.status,
       count(pll.ledger_line_id) AS posted_lines,
       count(DISTINCT pll.journal_entry_id) AS journals
FROM gl.fiscal_period fp
JOIN gl.fiscal_year fy ON fy.fiscal_year_id = fp.fiscal_year_id
LEFT JOIN gl.posted_ledger_line pll ON pll.fiscal_period_id = fp.fiscal_period_id
GROUP BY fy.year_code, fp.period_number, fp.status
ORDER BY fy.year_code, fp.period_number;

\echo '===== 9. Account classification coverage ====='
-- Every IFRS category must map to a module AccountClass, and every account type
-- to a module AccountKind. Anything unexpected here fails the backfill loudly.
SELECT ac.ifrs_category, a.account_type, count(*) AS accounts
FROM gl.account a
JOIN gl.account_category ac ON ac.category_id = a.category_id
GROUP BY ac.ifrs_category, a.account_type
ORDER BY 1, 2;

\echo '===== 10. Referential health that would stop the loader ====='
SELECT 'posted lines with no journal' AS finding, count(*)
FROM gl.posted_ledger_line pll
WHERE NOT EXISTS (SELECT 1 FROM gl.journal_entry je
                  WHERE je.journal_entry_id = pll.journal_entry_id)
UNION ALL
SELECT 'journal lines with no account', count(*)
FROM gl.journal_entry_line jel
WHERE NOT EXISTS (SELECT 1 FROM gl.account a
                  WHERE a.account_id = jel.account_id)
UNION ALL
SELECT 'categories whose parent is missing', count(*)
FROM gl.account_category c
WHERE c.parent_category_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM gl.account_category p
                  WHERE p.category_id = c.parent_category_id)
UNION ALL
SELECT 'duplicate journal numbers', count(*)
FROM (SELECT organization_id, journal_number
      FROM gl.journal_entry
      GROUP BY organization_id, journal_number HAVING count(*) > 1) dupes
UNION ALL
SELECT 'journal number blank or null', count(*)
FROM gl.journal_entry
WHERE journal_number IS NULL OR btrim(journal_number) = ''
UNION ALL
SELECT 'posted journals with zero lines', count(*)
FROM gl.journal_entry je
WHERE je.status = 'POSTED'
  AND NOT EXISTS (SELECT 1 FROM gl.journal_entry_line jel
                  WHERE jel.journal_entry_id = je.journal_entry_id)
ORDER BY 1;

\echo '===== 11. Dimension usage vs dimension masters ====='
-- ERP carries four FIXED dimension columns; the module carries a generic
-- registry read from the masters. A dimension id used on a line but absent from
-- its master is a value the registry cannot describe.
SELECT 'business_unit' AS dimension,
       (SELECT count(*) FROM core_org.business_unit)                                   AS master_rows,
       count(DISTINCT jel.business_unit_id)                                            AS used_on_lines,
       count(DISTINCT jel.business_unit_id) FILTER (
         WHERE jel.business_unit_id IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM core_org.business_unit m
                           WHERE m.business_unit_id = jel.business_unit_id))           AS used_but_unknown
FROM gl.journal_entry_line jel
UNION ALL
SELECT 'cost_center',
       (SELECT count(*) FROM core_org.cost_center),
       count(DISTINCT jel.cost_center_id),
       count(DISTINCT jel.cost_center_id) FILTER (
         WHERE jel.cost_center_id IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM core_org.cost_center m
                           WHERE m.cost_center_id = jel.cost_center_id))
FROM gl.journal_entry_line jel
UNION ALL
SELECT 'project',
       (SELECT count(*) FROM core_org.project),
       count(DISTINCT jel.project_id),
       count(DISTINCT jel.project_id) FILTER (
         WHERE jel.project_id IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM core_org.project m
                           WHERE m.project_id = jel.project_id))
FROM gl.journal_entry_line jel
UNION ALL
SELECT 'segment',
       (SELECT count(*) FROM core_org.reporting_segment),
       count(DISTINCT jel.segment_id),
       count(DISTINCT jel.segment_id) FILTER (
         WHERE jel.segment_id IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM core_org.reporting_segment m
                           WHERE m.segment_id = jel.segment_id))
FROM gl.journal_entry_line jel
ORDER BY 1;

\echo '===== 12. Trial balance, for the acceptance baseline ====='
-- The module''s trial balance must reproduce this exactly at the end of Gate D.
SELECT sum(debit_amount)  AS total_debit,
       sum(credit_amount) AS total_credit,
       sum(debit_amount) - sum(credit_amount) AS difference,
       count(*)           AS posted_lines,
       min(posting_date)  AS earliest_posting,
       max(posting_date)  AS latest_posting
FROM gl.posted_ledger_line;

\echo '===== survey complete (read-only; nothing was written) ====='
