-- audit_subledger_to_gl.sql
--
-- Subledger-to-GL reconciliation as of 2025-12-31 (FY2025 close).
--
-- For each control account, compares the subledger total (computed from
-- the source-of-truth domain tables) against the GL balance (computed
-- from gl.posted_ledger_line). Variances reveal where audit-prep work
-- is needed and how big each gap is.
--
-- Convention used here:
--   gl_balance = SUM(debit_amount) - SUM(credit_amount) for the account
--                across postings with posting_date <= 2025-12-31
--   variance   = subledger_total - gl_balance
--   |variance| < 1.00 NGN  ⇒ effectively reconciled (rounding)
--   |variance| > 1.00 NGN  ⇒ real gap; investigate
--
-- This is READ-ONLY. Run via:
--   psql "$DOTMAC_ERP_DB_DSN" -f scripts/audit_subledger_to_gl.sql
--
-- Sections:
--   1. AR Trade Receivables (1400)   ↔ ar.invoice + ar.customer_payment
--   2. AP Trade Payables (2000)      ↔ ap.supplier_invoice + ap.supplier_payment
--   3. FA cost (1100/1110/1120/1130) ↔ fa.asset.acquisition_cost
--   4. FA accumulated depreciation   ↔ fa.asset.accumulated_depreciation
--   5. Tax: Input VAT (1440)         ↔ tax.tax_transaction (INPUT)
--   6. Tax: Output VAT (4020)        ↔ tax.tax_transaction (OUTPUT)
--   7. Tax: Withholding (1420/2110)  ↔ tax.tax_transaction (WITHHOLDING)
--   8. Inventory (1300/1310)         ↔ inv.item_wac_ledger / inventory_valuation

\timing on
\pset pager off

\echo
\echo ============================================================
\echo  As-of date: 2025-12-31  (FY2025 close)
\echo ============================================================

\echo
\echo === SECTION 1 — AR Trade Receivables (1400) ===
WITH ar_subledger AS (
    SELECT COALESCE(SUM(
              GREATEST(COALESCE(i.functional_currency_amount, i.total_amount, 0)
                       - COALESCE(i.amount_paid, 0), 0)
           ), 0) AS subledger_total
    FROM ar.invoice i
    WHERE i.status NOT IN ('DRAFT', 'VOID')
),
ar_gl AS (
    SELECT COALESCE(SUM(debit_amount) - SUM(credit_amount), 0) AS gl_balance
    FROM gl.posted_ledger_line
    WHERE account_code = '1400'
      AND posting_date <= DATE '2025-12-31'
)
SELECT
    '1400 Trade Receivables' AS control_account,
    s.subledger_total,
    g.gl_balance,
    (s.subledger_total - g.gl_balance) AS variance,
    CASE WHEN ABS(s.subledger_total - g.gl_balance) < 1.00 THEN 'OK' ELSE 'INVESTIGATE' END AS status
FROM ar_subledger s, ar_gl g;

\echo
\echo === SECTION 2 — AP Trade Payables (2000) ===
WITH ap_subledger AS (
    SELECT COALESCE(SUM(
              GREATEST(COALESCE(i.functional_currency_amount, i.total_amount, 0)
                       - COALESCE(i.amount_paid, 0)
                       - COALESCE(i.prepayment_applied, 0), 0)
           ), 0) AS subledger_total
    FROM ap.supplier_invoice i
    WHERE i.status NOT IN ('DRAFT', 'VOID')
),
ap_gl AS (
    SELECT COALESCE(ABS(SUM(debit_amount) - SUM(credit_amount)), 0) AS gl_balance
    FROM gl.posted_ledger_line
    WHERE account_code = '2000'
      AND posting_date <= DATE '2025-12-31'
)
SELECT
    '2000 Trade Payables' AS control_account,
    s.subledger_total,
    g.gl_balance,
    (s.subledger_total - g.gl_balance) AS variance,
    CASE WHEN ABS(s.subledger_total - g.gl_balance) < 1.00 THEN 'OK' ELSE 'INVESTIGATE' END AS status
FROM ap_subledger s, ap_gl g;

\echo
\echo === SECTION 3 — Fixed Asset Cost (1100/1110/1120/1130) ===
WITH fa_subledger AS (
    SELECT COALESCE(SUM(COALESCE(functional_currency_cost, acquisition_cost, 0)), 0) AS subledger_total
    FROM fa.asset
    WHERE status IN ('IN_USE', 'NOT_IN_USE', 'FAULTY')
),
fa_gl AS (
    SELECT account_code,
           COALESCE(SUM(debit_amount) - SUM(credit_amount), 0) AS gl_balance
    FROM gl.posted_ledger_line
    WHERE account_code IN ('1100', '1110', '1120', '1130')
      AND posting_date <= DATE '2025-12-31'
    GROUP BY account_code
)
SELECT
    'FA Cost (1100+1110+1120+1130)' AS control_account,
    s.subledger_total,
    COALESCE(SUM(fg.gl_balance), 0) AS gl_balance,
    (s.subledger_total - COALESCE(SUM(fg.gl_balance), 0)) AS variance,
    CASE WHEN ABS(s.subledger_total - COALESCE(SUM(fg.gl_balance), 0)) < 1.00 THEN 'OK' ELSE 'INVESTIGATE' END AS status
FROM fa_subledger s
LEFT JOIN fa_gl fg ON true
GROUP BY s.subledger_total;

\echo
\echo --- (per-account FA cost detail) ---
SELECT account_code,
       COALESCE(SUM(debit_amount) - SUM(credit_amount), 0) AS gl_balance,
       COUNT(*) AS lines
FROM gl.posted_ledger_line
WHERE account_code IN ('1100', '1110', '1120', '1130')
  AND posting_date <= DATE '2025-12-31'
GROUP BY account_code
ORDER BY account_code;

\echo
\echo === SECTION 4 — Fixed Asset Accumulated Depreciation (1100-AD..1130-AD) ===
WITH fa_subledger AS (
    SELECT COALESCE(SUM(accumulated_depreciation), 0) AS subledger_total
    FROM fa.asset
    WHERE status IN ('IN_USE', 'NOT_IN_USE', 'FAULTY')
),
fa_gl AS (
    SELECT COALESCE(ABS(SUM(debit_amount) - SUM(credit_amount)), 0) AS gl_balance
    FROM gl.posted_ledger_line
    WHERE account_code IN ('1100-AD', '1110-AD', '1120-AD', '1130-AD')
      AND posting_date <= DATE '2025-12-31'
)
SELECT
    'FA Accum Dep (1100-AD..1130-AD)' AS control_account,
    s.subledger_total,
    g.gl_balance,
    (s.subledger_total - g.gl_balance) AS variance,
    CASE WHEN ABS(s.subledger_total - g.gl_balance) < 1.00 THEN 'OK' ELSE 'INVESTIGATE' END AS status
FROM fa_subledger s, fa_gl g;

\echo
\echo === SECTION 5 — Input VAT (1440) ===
WITH tax_subledger AS (
    SELECT COALESCE(SUM(functional_tax_amount), 0) AS subledger_total
    FROM tax.tax_transaction
    WHERE transaction_type = 'INPUT'
      AND transaction_date <= DATE '2025-12-31'
),
tax_gl AS (
    SELECT COALESCE(SUM(debit_amount) - SUM(credit_amount), 0) AS gl_balance
    FROM gl.posted_ledger_line
    WHERE account_code = '1440'
      AND posting_date <= DATE '2025-12-31'
)
SELECT
    '1440 Input VAT' AS control_account,
    s.subledger_total,
    g.gl_balance,
    (s.subledger_total - g.gl_balance) AS variance,
    CASE WHEN ABS(s.subledger_total - g.gl_balance) < 1.00 THEN 'OK' ELSE 'INVESTIGATE' END AS status
FROM tax_subledger s, tax_gl g;

\echo
\echo === SECTION 6 — Output VAT (4020 / 2120 VAT Payables) ===
WITH tax_subledger AS (
    SELECT COALESCE(SUM(functional_tax_amount), 0) AS subledger_total
    FROM tax.tax_transaction
    WHERE transaction_type = 'OUTPUT'
      AND transaction_date <= DATE '2025-12-31'
),
tax_gl AS (
    SELECT account_code,
           COALESCE(ABS(SUM(debit_amount) - SUM(credit_amount)), 0) AS gl_balance
    FROM gl.posted_ledger_line
    WHERE account_code IN ('4020', '2120')
      AND posting_date <= DATE '2025-12-31'
    GROUP BY account_code
)
SELECT
    'Output VAT (4020+2120)' AS control_account,
    s.subledger_total,
    COALESCE(SUM(g.gl_balance), 0) AS gl_balance,
    (s.subledger_total - COALESCE(SUM(g.gl_balance), 0)) AS variance,
    CASE WHEN ABS(s.subledger_total - COALESCE(SUM(g.gl_balance), 0)) < 1.00 THEN 'OK' ELSE 'INVESTIGATE' END AS status
FROM tax_subledger s
LEFT JOIN tax_gl g ON true
GROUP BY s.subledger_total;

\echo
\echo === SECTION 7 — Withholding Tax (1420 receivable + 2110 payable) ===
WITH wht_subledger AS (
    SELECT COALESCE(SUM(functional_tax_amount), 0) AS subledger_total
    FROM tax.tax_transaction
    WHERE transaction_type = 'WITHHOLDING'
      AND transaction_date <= DATE '2025-12-31'
),
wht_gl AS (
    SELECT account_code,
           COALESCE(SUM(debit_amount) - SUM(credit_amount), 0) AS gl_balance
    FROM gl.posted_ledger_line
    WHERE account_code IN ('1420', '2110')
      AND posting_date <= DATE '2025-12-31'
    GROUP BY account_code
)
SELECT
    'WHT (1420 + 2110)' AS control_account,
    s.subledger_total,
    COALESCE(SUM(ABS(g.gl_balance)), 0) AS gl_balance_abs_total,
    (s.subledger_total - COALESCE(SUM(ABS(g.gl_balance)), 0)) AS variance,
    CASE WHEN ABS(s.subledger_total - COALESCE(SUM(ABS(g.gl_balance)), 0)) < 1.00 THEN 'OK' ELSE 'INVESTIGATE' END AS status
FROM wht_subledger s
LEFT JOIN wht_gl g ON true
GROUP BY s.subledger_total;

\echo
\echo === SECTION 8 — Inventory (1300 Materials + 1310 Goods in Transit) ===
\echo  CAVEAT: inv.inventory_valuation has no historical snapshots. The closest
\echo  available subledger evidence is inv.item_wac_ledger which stores
\echo  CURRENT state only (one row per item x warehouse with total_value).
\echo  The subledger_total below is "as-of-now", NOT "as-of-2025-12-31".
\echo  Treat any non-zero variance as a structural reconciliation gap rather
\echo  than a true variance.
WITH inv_subledger AS (
    SELECT COALESCE(SUM(total_value), 0) AS subledger_total
    FROM inv.item_wac_ledger
),
inv_gl AS (
    SELECT account_code,
           COALESCE(SUM(debit_amount) - SUM(credit_amount), 0) AS gl_balance
    FROM gl.posted_ledger_line
    WHERE account_code IN ('1300', '1310')
      AND posting_date <= DATE '2025-12-31'
    GROUP BY account_code
)
SELECT
    'Inventory (1300+1310)' AS control_account,
    s.subledger_total,
    COALESCE(SUM(g.gl_balance), 0) AS gl_balance,
    (s.subledger_total - COALESCE(SUM(g.gl_balance), 0)) AS variance,
    CASE WHEN ABS(s.subledger_total - COALESCE(SUM(g.gl_balance), 0)) < 1.00 THEN 'OK' ELSE 'INVESTIGATE' END AS status
FROM inv_subledger s
LEFT JOIN inv_gl g ON true
GROUP BY s.subledger_total;

\echo
\echo ============================================================
\echo  Read each row: a non-zero variance means subledger and GL
\echo  disagree on what should be in that control account at year
\echo  end. The auditor will request a reconciliation schedule for
\echo  every account flagged INVESTIGATE before fieldwork.
\echo ============================================================
