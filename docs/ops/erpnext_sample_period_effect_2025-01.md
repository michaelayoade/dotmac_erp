# ERPNext Sample Period Effect: 2025-01

Method: backup-side activity measured by `2025-01` token hits in SQL INSERT lines (directional volume proxy, not exact row count). Target-side mapping measured from `sync.sync_entity` and core target tables for the same period.

## Summary
- Backup active doctypes: **207**
- Covered active doctypes (mapped in period): **19**
- Uncovered active doctypes: **188**
- Doctype coverage ratio: **9.2%**
- Backup covered token-hits: **57,058 / 613,224** (9.3%)
- `sync.sync_entity` mapped rows in period: **7,196** across **20** doctypes

## Target DB Counts (2025-01)
- ar.invoice: 3,978
- ap.supplier_invoice: 13
- ar.customer_payment: 3,078
- ap.supplier_payment: 1
- gl.journal_entry: 10,396
- support.ticket: 0
- hr.employee: 0

## Top Covered Doctypes (backup activity proxy)
- Payment Entry: 26,640
- Attendance: 6,924
- Stock Ledger Entry: 6,884
- Sales Invoice: 6,768
- Expense Claim: 4,221
- Task: 2,293
- Material Request: 1,784
- Purchase Invoice: 751
- Project: 356
- Item: 173
- Customer: 119
- Supplier: 65
- Employee: 34
- Warehouse: 13
- Item Group: 12
- Account: 11
- Designation: 6
- Department: 2
- Shift Type: 2

## Top Uncovered Doctypes (backup activity proxy)
- Version: 140,507
- GL Entry: 73,695
- Access Log: 35,836
- Comment: 34,853
- Notification Log: 32,826
- Payment Ledger Entry: 31,979
- Deleted Document: 23,913
- Communication Link: 19,044
- ToDo: 12,672
- Communication: 12,599
- Expense Claim Detail: 11,045
- HD Ticket Activity: 9,988
- Bank Transaction: 9,429
- Payment Entry Reference: 9,243
- File: 8,418
- PWA Notification: 8,320
- Stock Entry Detail: 7,356
- Bank Transaction Payments: 6,888
- Sales Invoice Item: 6,653
- HD Ticket: 5,717
- The Attendance: 5,563
- Payment Schedule: 5,259
- Material Request Item: 5,095
- Journal Entry: 4,732
- Sales Taxes and Charges: 4,376
- Data Import Log: 3,544
- View Log: 3,488
- Journal Entry Account: 2,000
- Salary Detail: 1,912
- Stock Entry: 1,777

Full table: `docs/ops/erpnext_sample_period_effect_2025-01.csv`
