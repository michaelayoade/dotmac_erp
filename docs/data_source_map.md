Data Source Map
===============

Legend
------
- GL (Posted Ledger): gl.posted_ledger_line (authoritative ledger)
- GL Cache: gl.account_balance (derived, not authoritative)
- GL Journal Metadata: gl.journal_entry (status, source metadata)
- Subledger: AR/AP/INV/etc operational tables
- Mixed: GL ledger lines with subledger joins for attribution (customer/supplier)

Dashboard
---------
- /dashboard
  - Stats: GL (Posted Ledger)
  - Revenue/Expenses trend: GL (Posted Ledger)
  - Cash flow (summary + chart): GL (Posted Ledger, cash-equivalent accounts)
  - Account Distribution: GL Cache with fallback to GL (Posted Ledger)
  - Top Customers: Mixed (GL revenue lines joined to AR invoices for customer attribution)
  - Top Suppliers: Mixed (GL expense lines joined to AP invoices for supplier attribution)
  - Posting Status: GL Journal Metadata (journal status by source document type)
  - Subledger Reconciliation: GL control vs subledger open balances

Reports
-------
- /reports (dashboard)
  - Assets/Liabilities/Equity/Revenue/Expenses/Net Income: GL (Posted Ledger)
  - AP/AR totals: GL control balances (Posted Ledger)
  - Tax summary: Tax subledger (operational)
- /reports/trial-balance: GL (Posted Ledger)
- /reports/income-statement: GL (Posted Ledger)
- /reports/balance-sheet: GL (Posted Ledger)
- /reports/general-ledger: GL (Posted Ledger)
- /reports/expense-summary: GL (Posted Ledger)
- /reports/ap-aging: Subledger (AP invoices)
- /reports/ar-aging: Subledger (AR invoices)
- /reports/tax-summary: Tax subledger (operational)

GL Module
---------
- /gl/journals, /gl/periods, /gl/accounts: GL (Posted Ledger + GL Cache)
  - Account balances in GL module may use GL Cache where available.

AR Module (Operational)
-----------------------
- /ar/* (customers, invoices, payments, aging): Subledger
  - Operational views with GL posting/reconciliation in the background.

AP Module (Operational)
-----------------------
- /ap/* (suppliers, invoices, payments, aging): Subledger
  - Operational views with GL posting/reconciliation in the background.

Inventory Module (Operational)
------------------------------
- /inv/* (items, stock, etc): Subledger

Expenses Module (Operational)
-----------------------------
- /exp/*: Subledger with GL posting adapters

Banking Module (Operational)
----------------------------
- /banking/*: Bank statements, reconciliations (operational), GL posting where applicable

Tax Module (Compliance)
-----------------------
- /tax/*: Tax subledger (compliance), reconciles to GL where applicable

FA / Lease / Financial Instruments
----------------------------------
- /fa/*, /lease/*, /fin-inst/*: Operational detail with GL posting adapters

Remaining Mismatches
--------------------
- None for financial reporting pages. All financial statements and dashboard totals
  now use GL (Posted Ledger) as the source of truth.
- Operational pages (AR/AP aging, invoice/bill detail, tax views) remain subledger-
  based by design; they are not used for financial statement totals.
