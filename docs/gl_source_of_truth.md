GL Source of Truth for Book Entry
=================================

Purpose
-------
Define a GL-first source-of-truth model for book entry and reporting.

Principles
----------
1) GL is the authoritative reporting source (posted ledger lines only).
2) Subledgers are operational detail and must reconcile to GL control accounts.
3) Corrections are made via new journals (adjustments/reversals), not edits.
4) Classification is controlled by the chart of accounts and must be stable.

Core Data Layers
----------------
GL (Source of Truth)
- gl.posted_ledger_line: immutable ledger lines (authoritative)
- gl.journal_entry / gl.journal_entry_line: posting inputs and metadata
- gl.account_balance: derived cache only (never authoritative)

Subledgers (Operational Detail)
- AR/AP/INV/FA/etc: invoices, bills, payments, inventory movements
- Each subledger document posts to GL via posting adapters

Tax Ledger (Supporting)
- Tax transactions may be created alongside posting
- Reporting still uses GL as the source of truth

Posting Rules
-------------
- Only POSTED journals are eligible for reporting.
- Journal entries are immutable after posting.
- Reversals or adjustments are separate journals.
- All journals must be balanced (debit = credit).
- Every journal line must map to a valid chart-of-accounts entry.

Required Traceability
---------------------
Each journal entry should carry:
- source_module (AR/AP/INV/FA/etc)
- source_document_type (e.g., INVOICE, SUPPLIER_INVOICE)
- source_document_id (subledger primary key)
- correlation_id (cross-module audit linkage)

Recommended GL line dimensions:
- business_unit_id, cost_center_id, project_id, segment_id

Reconciliation Rules
--------------------
Control accounts must reconcile:
- AR subledger open balance == GL AR control balance
- AP subledger open balance == GL AP control balance

Reconciliation is a required control, not a reporting source.

Reporting Rules
---------------
Financial Statements (GL-first):
- Trial Balance
- Income Statement
- Balance Sheet
- Cash Flow Statement

Operational Reports (Subledger detail):
- AR/AP aging
- Invoice/Bill listings
- Customer/Supplier detail

Data Flow (Simplified)
----------------------
Subledger Doc (Invoice/Bill/Payment)
  -> Posting Adapter
    -> gl.journal_entry + gl.journal_entry_line (POSTED)
      -> Financial Statements / Dashboards
    -> Reconciliation checks (subledger vs GL control)

Implementation Notes
--------------------
- If account_balance is empty, rebuild from posted journal lines.
- Do not use subledger totals for financial reporting.
- Keep account category (classification) consistent over time.
