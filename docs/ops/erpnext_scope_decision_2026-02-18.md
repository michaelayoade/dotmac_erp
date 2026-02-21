# ERPNext Scope Decision (Phase 1+2)

Based on:
- `docs/ops/erpnext_migration_gap_2026-02-18.csv`
- `docs/ops/erpnext_sample_period_effect_2025-01.csv`
- `docs/ops/erpnext_sample_day_effect_2025-01-06.csv`

## Bucket Counts
- migrate-now: **24**
- later: **291**
- ignore: **58**

## Migrate-Now (Top)
- Communication Link (Documents/Files): period=19044, day=1070, inserts=40
- HD Ticket Activity (Support): period=9988, day=398, inserts=10
- Bank Transaction (Banking/Treasury): period=9429, day=235, inserts=18
- File (Documents/Files): period=8418, day=236, inserts=24
- Bank Transaction Payments (Finance-GL): period=6888, day=50, inserts=10
- Payment Schedule (Finance-AR): period=5259, day=134, inserts=10
- Sales Taxes and Charges (Finance-AR): period=4376, day=109, inserts=12
- Salary Detail (HR Payroll): period=1912, day=0, inserts=3
- Sales Invoice Advance (Finance-AR): period=1704, day=63, inserts=2
- Salary Slip (HR Payroll): period=808, day=0, inserts=3
- Quotation (Finance-AR): period=427, day=26, inserts=2
- Employee Checkin (HR Core): period=324, day=30, inserts=1
- Quotation Item (Finance-AR): period=193, day=16, inserts=1
- Sales Order (Finance-AR): period=140, day=11, inserts=2
- Sales Order Item (Finance-AR): period=141, day=11, inserts=1
- Salary Structure Assignment (HR Payroll): period=60, day=2, inserts=1
- Purchase Invoice Advance (Finance-AP): period=19, day=1, inserts=1
- HD Ticket Type (Support): period=12, day=0, inserts=1
- HD Ticket Priority (Support): period=2, day=0, inserts=1
- Advance Taxes and Charges (Finance-AR): period=0, day=0, inserts=1
- HD Ticket Comment (Support): period=0, day=0, inserts=1
- HD Ticket Status (Support): period=0, day=0, inserts=1
- Issue Priority (Support): period=0, day=0, inserts=1
- Issue Type (Support): period=0, day=0, inserts=1

## Ignore (Top)
- Version (System/Logs): period=140507, day=13360, inserts=1200
- Access Log (System/Logs): period=35836, day=1592, inserts=106
- Notification Log (System/Logs): period=32826, day=998, inserts=225
- Deleted Document (Documents/Files): period=23913, day=998, inserts=179
- PWA Notification (System/Logs): period=8320, day=134, inserts=8
- View Log (System/Logs): period=3488, day=103, inserts=8
- Data Import Log (System/Logs): period=3544, day=0, inserts=22
- Scheduled Job Log (System/Logs): period=0, day=0, inserts=196
- DocField (Documents/Files): period=913, day=0, inserts=6
- DocShare (Documents/Files): period=579, day=22, inserts=1
- Email Queue (System/Logs): period=2, day=0, inserts=104
- Error Log (System/Logs): period=0, day=0, inserts=98
- Custom DocPerm (Other): period=210, day=30, inserts=1
- DocPerm (Documents/Files): period=129, day=0, inserts=1
- Document Share Key (Documents/Files): period=120, day=0, inserts=1
- Route History (System/Logs): period=3, day=0, inserts=22
- Webhook Request Log (System/Logs): period=0, day=0, inserts=22
- Dashboard Settings (Other): period=45, day=12, inserts=1
- DocType (Documents/Files): period=58, day=0, inserts=1
- Prepared Report (System/Logs): period=57, day=0, inserts=1

## Exact Backfill Sequence
1. Stabilize parent financial entities (already implemented): `./.venv/bin/python scripts/parallel_erpnext_sync.py all --workers 24 --org-id 00000000-0000-0000-0000-000000000001`
2. Add mapper + backfill for invoice tax/terms child doctypes: `Sales Taxes and Charges`, `Payment Schedule`, `Advance Taxes and Charges`, `Sales Invoice Advance`, `Purchase Invoice Advance`
3. Add mapper + backfill for quotation/order chain: `Quotation`, `Quotation Item`, `Sales Order`, `Sales Order Item`
4. Add mapper + backfill for banking linkage doctypes: `Bank Transaction`, `Bank Transaction Payments`
5. Add mapper + backfill for support activity doctypes: `HD Ticket Activity`, `HD Ticket Comment`, `Issue Type`, `Issue Priority`, `HD Ticket Type`, `HD Ticket Status`, `HD Ticket Priority`
6. Add mapper + backfill for payroll/HR operational doctypes: `Salary Slip`, `Salary Detail`, `Salary Structure Assignment`, `Employee Checkin`
7. Add mapper + backfill for document linkage doctypes: `File`, `Communication Link`
8. Re-run validation gates for sample day/month: count parity, amount parity (AR/AP/tax), unresolved-reference report = 0

## Notes
- `GL Entry` and `Payment Ledger Entry` are explicitly bucketed as `later` (archive/reconciliation-only) to avoid duplicate-ledger risk.
- System audit doctypes (for example `Version`, `Access Log`, `Notification Log`) are `ignore` by default.

Full decision table: `docs/ops/erpnext_scope_decision_2026-02-18.csv`
