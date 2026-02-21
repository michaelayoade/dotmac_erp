# ERPNext Sample Day Effect: 2025-01-06

Method: backup-side activity measured by `2025-01-06` token hits in SQL INSERT lines (directional proxy, not exact row count). Target-side mapping measured from `sync.sync_entity` and core target tables for the same day.

## Summary
- Backup active doctypes: **86**
- Covered active doctypes (mapped in day): **8**
- Uncovered active doctypes: **78**
- Doctype coverage ratio: **9.3%**
- Backup covered token-hits: **678 / 27,586** (2.5%)
- `sync.sync_entity` mapped rows in day: **169** across **8** doctypes

## Target DB Counts (2025-01-06)
- ar.invoice: 178
- ap.supplier_invoice: 1
- ar.customer_payment: 224
- ap.supplier_payment: 1
- gl.journal_entry: 503
- support.ticket: 0
- hr.employee: 0

## Top Covered Doctypes (backup activity proxy)
- Attendance: 248
- Sales Invoice: 167
- Stock Ledger Entry: 124
- Expense Claim: 96
- Purchase Invoice: 20
- Project: 11
- Customer: 7
- Item: 5

## Top Uncovered Doctypes (backup activity proxy)
- Version: 13,360
- GL Entry: 1,956
- Access Log: 1,592
- Comment: 1,178
- Communication Link: 1,070
- Deleted Document: 998
- Notification Log: 998
- Payment Ledger Entry: 857
- Payment Entry: 742
- ToDo: 504
- HD Ticket Activity: 398
- Communication: 328
- The Attendance: 263
- Expense Claim Detail: 246
- Sales Invoice Item: 246
- File: 236
- Bank Transaction: 235
- HD Ticket: 179
- Payment Entry Reference: 136
- PWA Notification: 134
- Payment Schedule: 134
- Journal Entry: 109
- Sales Taxes and Charges: 109
- View Log: 103
- Material Request Item: 95

Full table: `docs/ops/erpnext_sample_day_effect_2025-01-06.csv`
