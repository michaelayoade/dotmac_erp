\pset pager off
\set ON_ERROR_STOP on
\set ORG '00000000-0000-0000-0000-000000000001'
BEGIN;

CREATE TEMP TABLE fee AS
SELECT je.journal_entry_id, je.journal_number, je.status, je.entry_date,
       je.reference, je.correlation_id, je.posting_batch_id,
       je.total_debit_functional,
       CASE WHEN je.correlation_id ~ '^bank-fee-[0-9a-fA-F-]{36}$'
            THEN substring(je.correlation_id from 10)::uuid END AS line_id
FROM gl.journal_entry je
WHERE je.organization_id = :'ORG'::uuid AND je.source_document_type = 'BANK_FEE';
CREATE INDEX ON fee (line_id);

\echo '===== L1. Correlation-id health across ALL bank-fee journals ====='
SELECT status, count(*) AS journals,
       count(correlation_id) AS populated,
       count(*) FILTER (WHERE correlation_id IS NULL) AS null_correlation,
       count(*) FILTER (WHERE correlation_id IS NOT NULL AND line_id IS NULL) AS malformed,
       count(line_id) AS parsed_line_uuid,
       count(DISTINCT line_id) AS distinct_line_ids
FROM fee GROUP BY 1 ORDER BY 2 DESC;

\echo '===== L2. Do the parsed line ids resolve to a statement line? ====='
SELECT count(DISTINCT f.line_id) AS distinct_line_ids,
       count(DISTINCT f.line_id) FILTER (WHERE sl.line_id IS NOT NULL) AS resolve_to_statement_line,
       count(DISTINCT f.line_id) FILTER (WHERE sl.line_id IS NULL) AS dangling
FROM fee f LEFT JOIN banking.bank_statement_lines sl ON sl.line_id = f.line_id
WHERE f.line_id IS NOT NULL;

\echo '===== L3. Validate each journal against ITS OWN statement line ====='
\echo '-- organization, bank account, date, signed amount, GL account'
CREATE TEMP TABLE validated AS
SELECT f.journal_entry_id, f.journal_number, f.status, f.line_id,
       f.total_debit_functional, f.entry_date,
       st.organization_id AS line_org,
       ba.gl_account_id   AS line_bank_gl_account,
       sl.transaction_date, sl.amount AS line_amount,
       (SELECT min(l.account_id::text)::uuid FROM gl.journal_entry_line l
         WHERE l.journal_entry_id = f.journal_entry_id AND l.credit_amount_functional > 0) AS je_bank_account,
       (SELECT min(a.account_code) FROM gl.journal_entry_line l JOIN gl.account a ON a.account_id=l.account_id
         WHERE l.journal_entry_id = f.journal_entry_id AND l.debit_amount_functional > 0) AS je_debit_code
FROM fee f
JOIN banking.bank_statement_lines sl ON sl.line_id = f.line_id
JOIN banking.bank_statements st ON st.statement_id = sl.statement_id
JOIN banking.bank_accounts ba ON ba.bank_account_id = st.bank_account_id;

SELECT status, count(*) AS journals,
       count(*) FILTER (WHERE line_org = :'ORG'::uuid)                         AS org_matches,
       count(*) FILTER (WHERE je_bank_account = line_bank_gl_account)          AS bank_gl_account_matches,
       count(*) FILTER (WHERE entry_date = transaction_date)                   AS date_matches,
       count(*) FILTER (WHERE total_debit_functional = abs(line_amount))       AS amount_matches,
       count(*) FILTER (WHERE line_amount < 0)                                 AS line_is_a_debit_to_bank,
       count(*) FILTER (WHERE je_debit_code = '6080')                          AS debits_finance_cost
FROM validated GROUP BY 1 ORDER BY 2 DESC;

\echo '===== L4. THE AUTHORITATIVE POPULATION: per statement line ====='
CREATE TEMP TABLE per_line AS
SELECT v.line_id,
       count(*) FILTER (WHERE v.status='APPROVED') AS approved_journals,
       count(*) FILTER (WHERE v.status='POSTED')   AS posted_journals,
       count(*) FILTER (WHERE v.status NOT IN ('APPROVED','POSTED')) AS other_status,
       max(abs(v.line_amount)) AS fee_amount
FROM validated v GROUP BY 1;

SELECT count(*) AS distinct_statement_lines,
       sum(approved_journals) AS approved_journals,
       sum(posted_journals)   AS posted_journals,
       sum(fee_amount)        AS fee_value_once,
       sum(approved_journals * fee_amount) AS value_if_all_approved_posted
FROM per_line;

\echo '===== L5. Posting batches per line (the at-most-once boundary) ====='
SELECT count(DISTINCT pl.line_id) AS lines,
       sum((SELECT count(*) FROM gl.posting_batch b
            WHERE b.organization_id=:'ORG'::uuid
              AND b.idempotency_key LIKE '%'||pl.line_id::text||'%')) AS batches_on_those_lines
FROM per_line pl;

\echo '===== L6. EXACT excess posted count and amount ====='
SELECT count(*) FILTER (WHERE posted_journals = 0) AS lines_never_posted,
       count(*) FILTER (WHERE posted_journals = 1) AS lines_posted_once,
       count(*) FILTER (WHERE posted_journals > 1) AS lines_posted_more_than_once,
       sum(GREATEST(posted_journals - 1, 0)) AS excess_posted_journals,
       sum(GREATEST(posted_journals - 1, 0) * fee_amount) AS excess_posted_amount,
       sum(approved_journals) AS approved_awaiting,
       sum(approved_journals * fee_amount) AS amount_if_approved_were_posted
FROM per_line;

\echo '===== L7. Distribution of approved journals per line ====='
SELECT approved_journals, count(*) AS lines, sum(fee_amount * approved_journals) AS gross
FROM per_line GROUP BY 1 ORDER BY 1 DESC LIMIT 12;

\echo '===== L8. Does the heuristic bucket equal the true line? ====='
\echo '-- 111 buckets were claimed. This is how many statement lines they cover.'
SELECT count(DISTINCT (f.reference, f.entry_date, f.total_debit_functional)) AS heuristic_buckets,
       count(DISTINCT f.line_id) AS true_statement_lines
FROM fee f WHERE f.status='APPROVED';

\echo '===== L9. Lines per heuristic bucket — does one bucket hide many lines? ====='
WITH b AS (SELECT reference, entry_date, total_debit_functional,
                  count(DISTINCT line_id) AS lines_in_bucket
           FROM fee WHERE status='APPROVED' GROUP BY 1,2,3)
SELECT lines_in_bucket, count(*) AS buckets FROM b GROUP BY 1 ORDER BY 1 DESC LIMIT 10;
ROLLBACK;
