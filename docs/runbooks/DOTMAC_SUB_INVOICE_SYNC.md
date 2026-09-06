# Self-Care invoice synchronization

This runbook covers ERP's read/import boundary for Self-Care invoices. It does
not authorize changing invoice, tax, billing, journal, or cursor data directly.

## Signals

ERP exports these low-cardinality counters:

- `dotmac_sub_invoice_sync_rows_total{outcome="accepted"}`
- `dotmac_sub_invoice_sync_rows_total{outcome="tax_mapping_configuration"}`
- `dotmac_sub_invoice_sync_rows_total{outcome="source_accounting_mismatch"}`
- `dotmac_sub_invoice_sync_rows_total{outcome="database_error"}`
- `dotmac_sub_invoice_sync_rows_total{outcome="unexpected_row_error"}`
- `dotmac_sub_invoice_sync_limits_total`

Useful Grafana panels for a five-minute interval are:

```promql
sum by (outcome) (increase(dotmac_sub_invoice_sync_rows_total[5m]))
```

```promql
sum(increase(dotmac_sub_invoice_sync_rows_total{outcome!="accepted"}[15m]))
/
clamp_min(sum(increase(dotmac_sub_invoice_sync_rows_total[15m])), 1)
```

```promql
increase(dotmac_sub_invoice_sync_limits_total[30m])
```

Start with a ticket alert when at least 25 permanent rows fail in 15 minutes
and the non-accepted ratio exceeds 5%. Alert separately when any attempted-row
limit is reached twice in 30 minutes. Keep these as ticket-level data/config
signals; page only for `database_error`, authentication/transport failure, or a
worker hard timeout. Re-evaluate the thresholds after seven days of staging
and non-production observation rather than copying the incident's retry-amplified
log count into an alert threshold.

## `tax_mapping_configuration`

Self-Care and ERP own separate display-code namespaces. A source code such as
`VAT75` does not need to equal an ERP code such as `NG-VAT-7.5`. ERP resolves a
taxable line by these semantic facts:

1. organization and sales applicability;
2. percentage rate, excluding fixed-amount levies;
3. exclusive or inclusive application;
4. effective date range and active state; and
5. a configured collected-tax account.

Exactly one candidate is required. When several semantic candidates exist, an
exact source display-code match may break the tie; zero or unresolved multiple
candidates remain blocked.

Check the ERP tax-code screen for the affected date. Correct the ERP-owned
configuration through its normal service/UI. Do not rename Self-Care tax codes
or update invoice lines merely to make two display labels equal.

## `source_accounting_mismatch`

This means Self-Care's active line projection does not reconcile to its stored
invoice header. The v2 accounting projection supplies the durable issue code
and expected/actual evidence. Common issue codes are
`taxed_header_without_line_tax`, `header_subtotal_mismatch`, and
`legacy_header_totals_missing`.

Do not infer line tax from the header inside ERP. Review the v2 projection,
measure the unique invoice population by issue code, and use only an approved
Self-Care owner repair command. ERP should resume a corrected row when the
source `updated_at` changes.

## Validation after deployment

1. Run one bounded invoice phase in the named non-production environment.
2. Confirm the attempted-row count does not exceed the configured batch size.
3. Confirm repeated rows produce one representative error log per permanent
   failure class while the counter retains the full row count.
4. Confirm an unambiguous 7.5% exclusive ERP tax code with a collected-tax
   account admits a Self-Care `VAT75` line even when the ERP display code is
   different.
5. Confirm zero and ambiguous candidates remain blocked and no journal is
   created for those rows.
6. Compare accepted invoice AR, revenue, output-tax, and total values with the
   source projection before enabling any v2 posting cutover.

The durable issue ledger and cursor-advance behavior are a separate governed
change. Until that lands, permanent failures are retried on later runs, but the
attempt cap and log deduplication contain their operational impact.
