-- audit_tax_monthly_recon.sql
--
-- Month-by-month VAT and WHT reconciliation for 2025. Foundation for
-- filing the 12 monthly tax returns and providing audit evidence that
-- tax_transaction subledger ties to the GL movement on each control
-- account.
--
-- For each 2025 month:
--
--   INPUT VAT  : sum(functional_tax_amount) from tax.tax_transaction
--                where transaction_type='INPUT'
--                vs GL debit movement on 1440 Input VAT
--   OUTPUT VAT : sum(functional_tax_amount) from tax.tax_transaction
--                where transaction_type='OUTPUT'
--                vs GL credit movement on 4020 / 2120
--   WHT        : sum(functional_tax_amount) from tax.tax_transaction
--                where transaction_type='WITHHOLDING'
--                vs GL debit movement on 1420 (receivable) and credit on 2110 (payable)
--
-- Read-only. Run via:
--   psql "$DOTMAC_ERP_DB_DSN" -f scripts/audit_tax_monthly_recon.sql
--
-- A non-zero variance per month is a real reconciliation gap requiring
-- investigation before that month's return is filed.

\timing on
\pset pager off

\echo
\echo ============================================================
\echo  Tax monthly reconciliation: 2025 (subledger vs GL)
\echo ============================================================

-- Build a 12-row month axis. Each row joins subledger sums (from
-- tax.tax_transaction) and GL sums (from gl.posted_ledger_line) for
-- that month, then computes the variance per category.

WITH months AS (
    SELECT generate_series(
               DATE '2025-01-01',
               DATE '2025-12-01',
               INTERVAL '1 month'
           )::date AS m_start
),
month_bounds AS (
    SELECT m_start,
           (m_start + INTERVAL '1 month' - INTERVAL '1 day')::date AS m_end,
           TO_CHAR(m_start, 'YYYY-MM') AS yyyymm
    FROM months
),
sub AS (
    SELECT TO_CHAR(date_trunc('month', tt.transaction_date), 'YYYY-MM') AS yyyymm,
           SUM(CASE WHEN tt.transaction_type = 'INPUT'       THEN tt.functional_tax_amount ELSE 0 END) AS sub_input_vat,
           SUM(CASE WHEN tt.transaction_type = 'OUTPUT'      THEN tt.functional_tax_amount ELSE 0 END) AS sub_output_vat,
           SUM(CASE WHEN tt.transaction_type = 'WITHHOLDING' THEN tt.functional_tax_amount ELSE 0 END) AS sub_wht
    FROM tax.tax_transaction tt
    WHERE tt.transaction_date BETWEEN DATE '2025-01-01' AND DATE '2025-12-31'
    GROUP BY 1
),
gl AS (
    -- Operational movement only: excludes opening-balance journals
    -- (OB-000001 plus the Group E detail openings JE-2025-00012..00080).
    -- Without this filter the January row is dominated by the
    -- ~NGN 68M WHT opening carryforward and looks like a control bypass.
    SELECT TO_CHAR(date_trunc('month', pll.posting_date), 'YYYY-MM') AS yyyymm,
           SUM(CASE WHEN pll.account_code = '1440'                       THEN pll.debit_amount  ELSE 0 END)
             - SUM(CASE WHEN pll.account_code = '1440'                   THEN pll.credit_amount ELSE 0 END) AS gl_input_vat_net,
           SUM(CASE WHEN pll.account_code IN ('4020', '2120')            THEN pll.credit_amount ELSE 0 END)
             - SUM(CASE WHEN pll.account_code IN ('4020', '2120')        THEN pll.debit_amount  ELSE 0 END) AS gl_output_vat_net,
           SUM(CASE WHEN pll.account_code = '1420'                       THEN pll.debit_amount  ELSE 0 END)
             - SUM(CASE WHEN pll.account_code = '1420'                   THEN pll.credit_amount ELSE 0 END) AS gl_wht_recv_net,
           SUM(CASE WHEN pll.account_code = '2110'                       THEN pll.credit_amount ELSE 0 END)
             - SUM(CASE WHEN pll.account_code = '2110'                   THEN pll.debit_amount  ELSE 0 END) AS gl_wht_pay_net
    FROM gl.posted_ledger_line pll
    JOIN gl.journal_entry je ON je.journal_entry_id = pll.journal_entry_id
    WHERE pll.posting_date BETWEEN DATE '2025-01-01' AND DATE '2025-12-31'
      AND pll.account_code IN ('1440','4020','2120','1420','2110')
      AND je.journal_number NOT LIKE 'OB-%'
      AND je.description NOT ILIKE '%opening balance%'
      AND je.description NOT ILIKE '%op bal%'
    GROUP BY 1
)
SELECT mb.yyyymm AS month,
       COALESCE(sub.sub_input_vat,  0) AS sub_in_vat,
       COALESCE(gl.gl_input_vat_net, 0) AS gl_in_vat,
       COALESCE(sub.sub_input_vat,  0) - COALESCE(gl.gl_input_vat_net, 0) AS var_in_vat,
       COALESCE(sub.sub_output_vat,   0) AS sub_out_vat,
       COALESCE(gl.gl_output_vat_net, 0) AS gl_out_vat,
       COALESCE(sub.sub_output_vat,   0) - COALESCE(gl.gl_output_vat_net, 0) AS var_out_vat,
       COALESCE(sub.sub_wht, 0) AS sub_wht,
       COALESCE(gl.gl_wht_recv_net, 0) + COALESCE(gl.gl_wht_pay_net, 0) AS gl_wht_combined,
       COALESCE(sub.sub_wht, 0) - (COALESCE(gl.gl_wht_recv_net, 0) + COALESCE(gl.gl_wht_pay_net, 0)) AS var_wht
FROM month_bounds mb
LEFT JOIN sub USING (yyyymm)
LEFT JOIN gl  USING (yyyymm)
ORDER BY mb.yyyymm;

\echo
\echo === Annual totals (rolled up from above) ===
WITH sub_year AS (
    SELECT
        SUM(CASE WHEN transaction_type = 'INPUT'       THEN functional_tax_amount ELSE 0 END) AS sub_in,
        SUM(CASE WHEN transaction_type = 'OUTPUT'      THEN functional_tax_amount ELSE 0 END) AS sub_out,
        SUM(CASE WHEN transaction_type = 'WITHHOLDING' THEN functional_tax_amount ELSE 0 END) AS sub_wht
    FROM tax.tax_transaction
    WHERE transaction_date BETWEEN DATE '2025-01-01' AND DATE '2025-12-31'
),
gl_year AS (
    SELECT
        SUM(CASE WHEN pll.account_code='1440'             THEN pll.debit_amount  ELSE 0 END)
          - SUM(CASE WHEN pll.account_code='1440'         THEN pll.credit_amount ELSE 0 END) AS gl_in,
        SUM(CASE WHEN pll.account_code IN ('4020','2120') THEN pll.credit_amount ELSE 0 END)
          - SUM(CASE WHEN pll.account_code IN ('4020','2120') THEN pll.debit_amount ELSE 0 END) AS gl_out,
        SUM(CASE WHEN pll.account_code='1420'             THEN pll.debit_amount  ELSE 0 END)
          - SUM(CASE WHEN pll.account_code='1420'         THEN pll.credit_amount ELSE 0 END)
          + SUM(CASE WHEN pll.account_code='2110'         THEN pll.credit_amount ELSE 0 END)
          - SUM(CASE WHEN pll.account_code='2110'         THEN pll.debit_amount  ELSE 0 END) AS gl_wht
    FROM gl.posted_ledger_line pll
    JOIN gl.journal_entry je ON je.journal_entry_id = pll.journal_entry_id
    WHERE pll.posting_date BETWEEN DATE '2025-01-01' AND DATE '2025-12-31'
      AND pll.account_code IN ('1440','4020','2120','1420','2110')
      AND je.journal_number NOT LIKE 'OB-%'
      AND je.description NOT ILIKE '%opening balance%'
      AND je.description NOT ILIKE '%op bal%'
)
SELECT 'INPUT VAT'   AS category, sub_year.sub_in  AS subledger, gl_year.gl_in  AS gl, sub_year.sub_in  - gl_year.gl_in  AS variance FROM sub_year, gl_year
UNION ALL
SELECT 'OUTPUT VAT', sub_year.sub_out, gl_year.gl_out, sub_year.sub_out - gl_year.gl_out FROM sub_year, gl_year
UNION ALL
SELECT 'WITHHOLDING', sub_year.sub_wht, gl_year.gl_wht, sub_year.sub_wht - gl_year.gl_wht FROM sub_year, gl_year;

\echo
\echo === Tax periods status (filing readiness) ===
SELECT period_name, frequency, start_date, end_date, due_date, status
FROM tax.tax_period
WHERE start_date BETWEEN '2025-01-01' AND '2025-12-31'
ORDER BY start_date;

\echo
\echo ============================================================
\echo  Read each row of the monthly grid above as the basis for
\echo  that monthly tax return. var_in_vat / var_out_vat / var_wht
\echo  near zero means subledger and GL agree for the month.
\echo  Material variances must be resolved before the corresponding
\echo  return can be filed with confidence.
\echo ============================================================
