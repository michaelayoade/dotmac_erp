# Weekly Purchases report

Location: **Inventory > Reports > Stock Reports > Weekly Purchases**.
HTML: `/inventory/reports/weekly-purchases`; CSV: the same path plus `/export`.

## What is counted

This is an **invoice-date, posted-purchases report**, not a goods-received,
orders-placed, cash-paid, or stock-movement report. Its single source is supplier
invoice lines linked to inventory-tracked INVENTORY or KIT items. Purchase
orders, receipts and stock transactions are not added to invoice quantities,
preventing duplicate counting across a purchase's lifecycle.

Included states are POSTED, PARTIALLY_PAID and PAID. ON_HOLD and DISPUTED invoices
are included only when their separate posting status is POSTED. Draft,
submitted, pending approval, approved-but-unposted, rejected, void and
prepayment invoices are excluded. Non-stock/service items and lines without a
matching stocked item are excluded.

STANDARD invoices and DEBIT_NOTE lines contribute their recorded amounts.
CREDIT_NOTE amounts and taxes are normalized to negative absolute values,
shown separately as credits and deducted once. A credit is a financial
adjustment, not confirmation that physical stock was returned.

An uninvoiced receipt does not appear here; an invoiced item can appear before
receipt. Use Stock Movement or Yearly Stock Movement to inspect physical stock.
Late posting or backdating can change an older week. This is a live report, not
an immutable weekly snapshot or an automatically emailed report.

## Period, filters and totals

- Weeks run Monday through Sunday. Today is resolved in Africa/Lagos. Invoice
  dates are accounting dates, not timestamps to shift between timezones. Any
  selected date normalizes to Monday. The current week is **week to date**,
  excluding future-dated invoices. Future weeks and dates before 1900 are
  rejected.
- Supplier, receipt warehouse, category and literal text search filters persist
  through week navigation, pagination and export. `%` and `_` are searched as
  literal text, not SQL wildcard characters.
- Receipt warehouse comes from the invoice line, falling back to its explicitly
  linked goods receipt. Unspecified warehouses remain included under All.
  Inactive master records remain visible and selectable for historical weeks.
- Totals cover all matching lines, not only the 50 displayed rows. Purchases,
  credits, net and tax are separated by original invoice currency. No mixed
  currency grand total or implicit currency conversion is shown.
- Quantity is the invoice-line quantity. Invoice lines do not carry a UOM
  snapshot, so the report does not infer a historical purchase unit from today's
  item master or add quantities across unrelated items. Item descriptions,
  categories and stock-tracking eligibility reflect current master records.
- CSV includes every matching line with source decimal precision, net values
  before/after tax, and a UTF-8 BOM. Exports exceeding 50,000 lines return an
  instruction to narrow filters rather than silently truncating the result.

## Access and architecture

Both endpoints require inventory module access and `inventory:stock:read`,
using `get_db_for_org` for tenant context. Invoice data and joined master records
are explicitly organization-scoped. Invoice links require finance module access
and `ap:invoices:read`; the destination retains its own checks. HTML and CSV are
non-cacheable. CSV labels and references are protected against spreadsheet
formula interpretation; numeric values remain numeric.

The query service, web adapter and thin router are separate. `app/main.py`
registers the router inside the existing inventory enablement/license gate.
The reports hub includes a permission-aware card partial. Native GET forms work
without JavaScript; HTMX updates the whole report section so dates, totals,
pagination and export links stay synchronized.

No new tables, migrations, dependencies, permission grants, scheduled jobs or
stock/accounting mutations are introduced.

## Verification

Run inside the repository's pinned Poetry environment:

```sh
poetry run pytest tests/test_inventory_weekly_purchases.py tests/test_inventory_weekly_purchases_web.py tests/test_inventory_weekly_purchases_registration.py -q
poetry run ruff check app/services/inventory/weekly_purchases.py app/services/inventory/weekly_purchases_web.py app/web/inventory_weekly_purchases.py tests/test_inventory_weekly_purchases*.py
poetry run ruff format --check app/services/inventory/weekly_purchases.py app/services/inventory/weekly_purchases_web.py app/web/inventory_weekly_purchases.py tests/test_inventory_weekly_purchases*.py
poetry run mypy app/services/inventory/weekly_purchases.py app/services/inventory/weekly_purchases_web.py app/web/inventory_weekly_purchases.py
```

Query tests build a SQLite projection from real ORM column types and exercise
the actual report SQL: dates, eligibility, credits in either sign, currencies,
multiple lines per invoice, receipt joins, inactive records, tenant boundaries,
literal search, pagination and export limits. Web tests cover dependencies,
input validation, filter-preserving links, empty/populated templates, escaping,
and CSV precision. Registration tests check the module gate and card visibility.
These tests do not substitute for the full pinned suite, PostgreSQL/RLS checks,
query-plan review, or browser acceptance using the shared UI package.

## Rollout

After CI and review, merge and deploy through the existing application pipeline.
In staging, verify the hub card as an authorized inventory user. Compare a
closed week and the current week to posted AP stock-item lines, including a
credit note and a purchase without a warehouse. Exercise each filter and compare
CSV totals with the screen. Verify unauthorized users cannot access either
endpoint, and the routes remain absent when the inventory module is disabled.

Reverting this feature removes only the report and navigation. No data rollback
is needed.
