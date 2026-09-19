# Weekly Purchases compatibility

Weekly Purchases has been expanded into **Purchase Report** under
Inventory > Reports > Stock Reports. See [Purchase Report](purchase-report.md)
for the authoritative coverage, period, reconciliation, export and access rules.

The original `/inventory/reports/weekly-purchases?week_start=2026-09-14` link and
its `/export` endpoint remain valid. They open/export the new report with a
weekly selection, including all saved eligible invoice lines, not just stock.

The canonical page is `/inventory/reports/purchases`. It supports Weekly,
Monthly, Quarterly and Yearly reporting. Both URL families require inventory
module access, `inventory:stock:read` and `ap:invoices:read` because the expanded
report includes non-stock supplier expenses. No permissions are granted by the
feature. Existing invoice/stock entry, approval and posting remain unchanged.
