# Purchase Report

Location: **Inventory > Reports > Stock Reports > Purchase Report**.
Canonical HTML: `/inventory/reports/purchases`; CSV: `/inventory/reports/purchases/export`.

## Periods and compatibility

Select Weekly, Monthly, Quarterly, or Yearly. Weeks are Monday-Sunday;
quarters and years are calendar periods (Q1 Jan-Mar through Q4 Oct-Dec).
Africa/Lagos determines today. Current periods stop at today and are labelled
period-to-date; historical periods cover their complete range. Future periods
are rejected. Supported dates begin on 1 January 1900.

Canonical query example: `?period=monthly&period_start=2026-09-01`.
Month/year and quarter/year form controls normalize into one period-start date.
Navigation and exports preserve supplier, warehouse, category and search filters.
Changing a filter resets pagination; Clear filters preserves the selected period.

Existing `/inventory/reports/weekly-purchases?week_start=2026-09-14` and its
`/export` endpoint remain valid aliases. They use the new **all-line** adapter,
not the former stock-only query. Subsequent navigation uses the canonical URL.
Legacy Python weekly query/web helpers remain import-compatible; no mounted
report route calls the old stock-only web adapter. Common decimal/CSV helpers
are reused. The existing module mount in `app/main.py` is unchanged.

## Purchase recording and coverage

The financial source is `ap.supplier_invoice` and `ap.supplier_invoice_line`,
using invoice date rather than creation, payment, posting or receipt date.
Every saved line on an eligible invoice counts: stock, non-stock, service,
manually described items and separately recorded delivery/installation charges.
An invoice made entirely of unlinked descriptions also appears. Items,
categories, warehouses and suppliers are organization-scoped optional enrichment;
inactive records remain usable and missing/foreign metadata is never disclosed.
The saved description is primary; links are shown only for matched item records.
Search includes descriptions; `%` and `_` are literal. All includes unspecified
warehouses/categories; explicit Unspecified filters are available.

Eligibility remains POSTED/PARTIALLY_PAID/PAID, plus ON_HOLD/DISPUTED only when
posting_status is POSTED. Draft, submitted, approved-but-unposted, void, rejected
and prepayment invoices are excluded. A stock receipt without an eligible
invoice is not a billed purchase. Orders, receipts and payments are not unioned
into the report. Credit amounts/taxes are normalized to negative absolute values
once. The report does not infer a physical stock return from a credit note.

## Amounts and reconciliation

Use saved line_amount and tax_amount, not today's catalogue price or a new
quantity-times-price calculation. Existing tax-inclusive calculations therefore
remain authoritative. No quantities are summed across unrelated items/units.
All currency totals are separate; no implicit exchange conversion is performed.

Matching line totals cover ALL filtered results, not just the 50 displayed lines.
Full invoice totals count each matched invoice once, even where two invoices have
the same amount. Reconciliation compares each stored invoice total with the sum
of ALL its saved lines including tax, independently of filters and pagination.

A category/warehouse/search filter can legitimately select only part of an
invoice: matching totals and full totals are labelled separately. Non-zero
reconciliation differences are flagged; no invoice values are modified. The
mismatch count also catches opposing differences that cancel in a currency sum.
Invoice details below the table cover invoices represented on the current page;
their totals include saved lines on other pages. A discrepancy is a review flag,
not proof of its cause: investigate imported/header-only adjustments separately.
This cannot recover invoice lines that were never saved. Header-only invoices
with no saved lines have no matching lines and do not enter this projection.
Late/backdated posting can change older periods; this is a live report, not a
frozen accounting snapshot or an automatic email report.

## CSV and permissions

Item lines CSV exports all matching lines, with full stored decimal precision and
period/start/end/through metadata. It deliberately does not repeat header totals
on line rows. Invoice totals CSV (`view=invoices`) exports one row per matched
invoice, including matching/full line counts, matching/full line totals, stored
invoice total and difference. Each export refuses more than 50,000 rows rather
than silently truncating. Narrow filters for larger datasets.

Both URL families and all exports require inventory module access,
`inventory:stock:read` AND `ap:invoices:read`. This additional AP permission is
intentional because all-line reporting exposes supplier expenses. The hub card
uses both permissions. No access is granted automatically. The tenant database
dependency and explicit organization predicates remain; text is HTML-escaped and
CSV-formula-protected. Responses are no-store. Finance invoice links retain their
own destination permissions and appear only with finance module access.

## Validation and rollout

Run in the repository's pinned environment:

```sh
poetry run pytest tests/test_inventory_purchase_report.py tests/test_inventory_purchase_report_web.py tests/test_inventory_weekly_purchases.py tests/test_inventory_weekly_purchases_web.py tests/test_inventory_weekly_purchases_registration.py -q
poetry run ruff check app/services/inventory/purchase_report*.py app/web/inventory_weekly_purchases.py tests/test_inventory_purchase_report*.py tests/test_inventory_weekly_purchases_registration.py
poetry run ruff format --check app/services/inventory/purchase_report*.py app/web/inventory_weekly_purchases.py tests/test_inventory_purchase_report*.py tests/test_inventory_weekly_purchases_registration.py
poetry run mypy app/ --no-incremental
```

New query tests use projections of real ORM column types and the actual SQL.
They cover period boundaries, eligibility, missing items, services, tax/credits,
foreign metadata, duplicate-valued invoices, partial filters, discrepancy counts,
pagination and export limits. HTTP/Jinja tests cover all four periods, AP guards,
legacy URLs, safe descriptions, navigation and CSV precision. These do not replace
PostgreSQL/RLS, shared-UI browser acceptance or production-data reconciliation.

Before deployment, compare closed/current periods with AP invoices in staging,
including an unlinked-only invoice, mixed invoice, credit and known discrepancy.
Check the annual query plan on representative volume; add a targeted date index
only with evidence and a migration. Verify both permissions, old links, filter
switching, keyboard use and both exports. Normal CI/review/deployment gates apply.

No tables, migrations, dependencies, workflows, permission grants, catalogue
creation or stock/accounting writes are introduced. Reverting the PR restores the
previous report without any data rollback.
