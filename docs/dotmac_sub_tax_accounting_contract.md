# Dotmac Sub tax and accounting contract

## Decision

Dotmac Sub owns ISP billing facts. Dotmac ERP owns accounting.

This boundary is intentionally asymmetric: ERP pulls immutable or versioned
source facts from Sub, maps them through ERP configuration, and creates the
canonical accounting projection. Sub must not contain a parallel chart of
accounts, balanced journal, tax-return ledger, or financial-statement path.

Tax identity remains system-local. A tax identifier held for a subscriber in
Sub is not ERP's customer tax-identification record and is not copied into it.
ERP users or an ERP-owned validation/FIRS integration establish and govern the
ERP customer, supplier, and organization tax identities independently. A
versioned integration may carry a source tax identifier as explicit evidence
when a concrete accounting process requires it, but it does not silently
promote that observation into ERP master data.

Legacy imported values are not cleared automatically because their provenance
was not retained and some may have since been independently verified in ERP.
They require an ERP-owned Finance audit before FIRS validation or filing use.

| Concern | Owning system | Canonical record |
|---|---|---|
| Customer charge, invoice and credit-note lines | Sub | Billing document and line |
| Tax rate selected and inclusive/exclusive/exempt treatment | Sub | Line `tax_rate_id` and `tax_application` |
| Payment gross cash-settlement facts | Sub | Payment plus WHT record |
| WHT evidence and lifecycle decision | Sub | WHT record and append-only transition timeline |
| TaxCode, jurisdiction and account mapping | ERP | ERP TaxCode |
| AR subledger projection | ERP | ERP invoice, credit note and customer payment |
| Balanced journal and posted ledger | ERP | Journal entry and posted ledger lines |
| Tax transaction, tax return and financial statement | ERP | ERP tax subledger and GL |

## Pull contract

ERP uses the existing Dotmac Sub pull integration. No second push/outbox path is
permitted for the same accounting decisions.

Invoice and credit-note lines provide:

- source document and line IDs;
- quantity, unit price and source amount;
- `tax_rate_id` when taxable;
- `tax_application`: `exclusive`, `inclusive`, or `exempt`;
- header subtotal, tax total and total for reconciliation.

Payments provide:

- source payment ID, currency and settlement time;
- gross amount, net bank amount, WHT amount and WHT percentage;
- WHT source-record ID, evidence status and certificate reference;
- WHT resolution timestamp for a terminal `reclaimed` or `written_off` state.

For every WHT settlement:

`net amount + WHT amount = gross amount`

The imported document is not accepted if its source arithmetic does not
reconcile.

## ERP mapping and posting rules

Sub rates are percentages (for example `7.5`); ERP TaxCode rates are ratios
(for example `0.075`). ERP resolves an active, effective TaxCode with the exact
source code/rate, tax type, sales applicability and inclusive mode.

Mapping is fail-closed. A missing or ambiguous TaxCode, missing revenue/control
account, missing WHT account, mismatched line/header total, or failed GL posting
rolls back that source row. ERP does not guess a default VAT code or maintain a
single hard-coded tax rate.

The normal WHT receipt is:

- debit Bank for net cash;
- debit ERP TaxCode `tax_paid_account_id` for WHT receivable;
- credit AR control for gross settlement;
- record the WHT tax transaction in ERP.

Invoice allocation capacity is the gross AR settlement, not the net bank leg.
Manual and imported customer payments both delegate journal and tax-transaction
construction to the canonical ERP AR posting adapter.

Terminal Sub decisions have ERP-owned consequences dated with the source
resolution timestamp:

- `reclaimed`: debit the ERP WHT tax-liability/clearing account
  (`tax_collected_account_id`) and credit WHT receivable;
- `written_off`: debit the ERP WHT expense account (`tax_expense_account_id`),
  credit WHT receivable, and append a negative WHT tax transaction so the
  unusable tax credit is removed from the current reporting period.

## Corrections and reconciliation

Posted accounting is never rewritten in place. A material source correction
reverses the prior ERP journal and posts the corrected projection. Import hashes
optimize pulls but do not own reconciliation: an unchanged source row may repair
a missing ERP journal or terminal WHT consequence.

All posting paths are idempotent by source document and action. ERP account
configuration is the only writer of account mappings; Sub changes source facts
or WHT decisions and ERP reconciles their consequences.
