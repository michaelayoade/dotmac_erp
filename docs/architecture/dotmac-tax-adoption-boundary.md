# `dotmac-tax` adoption boundary

Status: **C2 composed and disabled; no tax authority has moved**
ERP C2 base: `7b62974b366eead1b32bead380e47d9cf10ec4c7`
Released module: `dotmac-tax 0.1.0a3`
Release run: `32898397980`; annotated tag `dotmac-tax-v0.1.0a3`
peels to Starter `531f7f8c37ce2fdf41ecbf2f9a7a9940264a18f9`.
Release record PR #443 merged at
`fca290ac7a32755e9ab000661e8bc6a35c138173`; its post-merge CI and
Engineering Standards passed. ERP pins that exact artifact and composes
`tx_0003_result_fingerprint`, while `TAX_COMPOSITION_ENABLED` remains false.

## Outcome

`dotmac-tax` becomes ERP's one tax-policy, determination, statutory-report and
return-lifecycle owner. VAT is one configured tax code, not a special product
flag. The same owner supports withholding, stamp duty, fixed levies, excise,
customs, income/payroll and organization-defined tax codes as data.

ERP remains the accounting owner. It supplies versioned source facts to the
module, consumes immutable determination components, selects ERP-owned account
mappings, writes document snapshots and tax-subledger consequences, and asks
the accounting owner to post balanced journals. The module never imports ERP
invoice, payment, payroll, tax-transaction or GL models.

Composition is local. ERP uses its existing same-UUID mapping
`tenant_id = organization_id`, pins the released package, runs the independent
`tx` lineage in its own database and binds the module prerequisites to ERP's
existing provider revisions. It does not read Starter's or another product's
database.

## Current ERP authority to migrate

### Policy and calculation

| Current source | Current decision | Target |
|---|---|---|
| `app/models/finance/tax/tax_code.py` | Rate, fixed/percentage overload, inclusive, compound, recovery, applicability, return box and GL accounts on one row | Module TaxCode + effective TaxRule own calculation/report identity; ERP link owns GL accounts |
| `TaxType` | Fixed Python vocabulary (`VAT`, `WITHHOLDING`, `STAMP_DUTY`, etc.) | Open `tax_kind_code` data; no enum migration for a custom tax |
| `app/models/finance/tax/tax_jurisdiction.py` | Carries a parallel current/future rate | Jurisdiction owns place/authority/currency only; effective rules own every rate |
| `app/services/finance/tax/tax_calculation.py` | Multiple, inclusive, fixed and compound calculation | `determine_tax_set`; no retained fallback calculator |
| `app/models/finance/tax/fiscal_position.py` and its service | Partner/place matching and tax remapping; `tax_dest_id=NULL` erases a tax | Effective tax-specific party/supply/place classifications plus explicit `zero_rated`, `exempt` or `out_of_scope` rules |
| payroll PAYE/tax-band calculators | Establish taxable payroll base and calculate statutory tax | Payroll retains taxable-base/relief facts; module owns the progressive tax rule and determination |
| hard-coded or seeded statutory data | Rates, bands, calendars, boxes and due dates | Evidenced operator data backfilled into module authorities, codes, rules, classifications, report definitions and obligations |

The existing calculator orders ordinary taxes before compound taxes and then by
tax-code text. The target requires an explicit positive calculation sequence
and an explicit `source_amount` or `source_plus_prior_tax` base for every code.
Alphabetical order is not migration evidence.

ERP currently permits more than one inclusive tax on a line and extracts them
sequentially. The released a3 contract deliberately refuses an inclusive component
combined with any other component because the source amount is otherwise
ambiguous. Any live ERP row using that combination is an adjudication blocker,
not an expected variance to normalize away.

### Determination consumers and consequences

| Fact family / reader | Current path | Cutover consequence |
|---|---|---|
| AR invoice and credit-note lines | `app/services/finance/ar/invoice.py`, AR posting | Determine output tax; freeze component ids/versions/treatment/base/tax on the document line; ERP posts revenue, receivable and tax liability |
| AP supplier invoice and credit-note lines | `app/services/finance/ap/supplier_invoice.py`, AP posting | Determine input tax and recoverability; ERP posts expense/asset, payable, recoverable tax and non-recoverable expense |
| Recurring AR/AP generation | `app/services/finance/automation/recurring.py` | Submit the same typed line fact; do not retain a recurring-only calculator |
| Customer/supplier settlement WHT | AR/AP payment and posting helpers | Determine withholding from gross settlement evidence; ERP preserves `net + WHT = gross` and posts receivable/payable consequences |
| Stamp duty and other fixed levies | AP/AR posting paths | Use a typed fixed-money rule, never overload a ratio field |
| Expense tax | expense service/posting | Determine from the expense line fact and freeze the component snapshot |
| Sub accounting import | `app/services/dotmac_sub/sync/*` | Record typed external observations, map them to local module facts/policy versions and reconcile; source tax ids remain evidence, never ERP master ids |
| Payroll | payroll tax calculators and GL adapter | Payroll supplies the legally defined taxable base and employee classification references; module determines PAYE components; ERP posts payroll consequences |
| Deferred/current tax | deferred-tax services and tax posting adapter | Retain accounting measurement inputs in ERP; use module code/rule identity where the fact is a tax determination; ERP owns provisions and deferred-tax journals |
| Tax summaries, periods and returns | tax reports, period, return and compliance services | Module report definitions/boxes, filing obligations, snapshots and return events become authoritative; ERP UI/analytics become projections |

Issued invoices, posted journals, filed returns and prior tax-transaction rows
are immutable historical evidence. Migration links or reverses/corrects them; it
does not rewrite their amounts in place.

## Typed ERP seams

The adopter introduces two ERP-owned contracts. They are not free-form payloads
and routes/tasks never construct module ORM rows.

### `ERPSourceTaxFactV1`

Required fields:

- `organization_id` and the derived same-UUID module tenant scope;
- source family, document id, line id where applicable, source version and an
  immutable evidence reference;
- occurred date, recognition basis and transaction side;
- exact non-negative amount, ISO currency and minor units;
- party, supply and place references (opaque to the module); and
- the caller's idempotency/correlation evidence.

One mapper per named fact family translates that contract into public
`dotmac_tax.TaxFact`. Product adapters do not choose a rate or treatment. An
observation with missing or ambiguous classification evidence fails closed and
enters a review worklist.

### Public `TaxDeterminationSetV1` + ERP `TaxApplicationContextV1`

The module owns and publishes the immutable result shape. It carries source
and result fingerprints, set/component/line identities, ordered code/rule/
classification evidence, exact source/net/tax/gross and recovery amounts,
currency and aware determination time. ERP does not restate that contract.

`TaxApplicationContextV1` adds only ERP-owned application facts: organization,
posting/document/line identity, accounting consequence, counterpart account,
an explicit exact exchange rate, reversal and fiscal dimensions. The
consequence must agree with the module transaction side (`output`, `input`,
`withholding`, or `liability`) or the entire projection is refused.

The C2 consequence adapter is pure: it verifies the module fingerprint and
arithmetic, resolves an unambiguous effective ERP account mapping for every
component and returns a complete balanced posting proposal. At cutover, the
local owning service will write the document/tax-transaction snapshot and call
the accounting owner in the same transaction. Missing mappings, closed fiscal
periods, currency mismatches, duplicate source versions or changed fingerprints
refuse the whole source row. A module determination never writes GL directly.

## Backfill mapping

Every current active or historically referenced ERP policy row is classified
before it can be projected:

| ERP evidence | Module projection | ERP-retained evidence |
|---|---|---|
| Tax jurisdiction | authority + jurisdiction | organization registration and external authority references |
| Tax code/type | code + open `tax_kind_code` | effective account mapping keyed to module tax-code id |
| percentage rate | percentage rule | historical document line snapshot |
| `is_fixed_amount` + `tax_rate` | fixed rule with typed exact money | source-row fingerprint proving the overloaded legacy representation |
| `is_compound` | explicit sequence + `source_plus_prior_tax` | approved ordering adjudication |
| inclusive | inclusive percentage rule | document's inclusive/exclusive source evidence |
| recovery flags/rate | rule recoverable rate | ERP account mapping and postings |
| fiscal position match | tax-specific party/supply/place classification | local customer/supplier identity and verification evidence |
| fiscal-position null destination | explicit zero/exempt/out-of-scope rule | legal basis and approver; never “removed tax” |
| return box/reporting code | report definition and box | filing transport and ERP UI projection |
| tax period/due date | filing obligation | reminders and operator assignment projection |

Rows with missing legal basis, overlapping effective rules, an unspecified
component order, multiple plausible accounts, mixed currencies, unsupported
inclusive combinations or a closed-enum value that cannot be mapped are
`operator_adjudication_required`. No default VAT rate, generic “OTHER” type or
zero amount is used to make the backfill pass.

## Shadow cohorts and gates

Cut over in this order so statutory reporting remains a downstream check over
already-proven determinations:

1. AR invoice/credit-note line tax;
2. AP invoice/credit-note line tax and recoverability;
3. recurring invoice generation;
4. customer and supplier WHT settlements;
5. expenses, stamp duty and other fixed/custom levies;
6. payroll and deferred/current-tax fact families;
7. Sub-imported accounting observations; and
8. report boxes, filing obligations and return lifecycle.

For each cohort, backfill policy and classification data first, then shadow the
old engine against module output. Compare, per source line:

- selected tax code and rule version;
- party/supply/place classification and treatment;
- calculation sequence/base and inclusive flag;
- base, tax, recoverable, non-recoverable, net and gross amounts;
- document/header arithmetic and WHT `net + WHT = gross`;
- ERP account mapping and proposed balanced journal; and
- report box, period, payable total and obligation where applicable.

The gate is zero unexplained mismatches, zero missing/duplicate/ambiguous
policies or classifications, zero fingerprint conflicts, complete source-row
coverage and approval of the fingerprinted run by Finance/Tax. A known,
documented legal difference can be approved as an expected difference; it is
never hidden by changing comparison tolerances.

The cohort's writer and every reader switch together. The old calculator is
then sealed for that family and deleted when no cohort uses it. Runtime flags
may select the one active authority during a bounded rollout; they may not leave
two calculators live as permanent fallback.

## Composition and release gates

C2 satisfied the external release oracle before changing ERP: release run
`32898397980` published and installed back a3, the annotated tag peeled to
`531f7f8c37ce2fdf41ecbf2f9a7a9940264a18f9`, and generated release-record PR
#443 merged and passed post-merge CI.

ERP now pins the exact release, composes the `tx` lineage, binds
`tenant_scope_catalog.v1` and `module_database_roles.v1` to its existing ERP
providers, and proves `alembic upgrade heads` on a fresh disposable PostgreSQL
database. Composition creates storage only; no authority moves until a cohort
passes its shadow and cutover gate.

Foreign-source-currency rows remain outside the admissible C3 cohort. ERP must
not assume that an invoice-header exchange rate is the legal tax-base rate.
Until Finance/Tax approves the legal base currency, rate owner/type/event date,
rounding and retained evidence, those rows produce adjudication evidence and no
module call. This does not block C2 or a jurisdiction-currency-only C3 shadow.

## Source-of-truth documents changed at cutover

- `docs/SOT_RELATIONSHIP_MAP.md` names module tax policy/determination/report/
  return ownership and ERP accounting-consequence ownership.
- `docs/dotmac_sub_tax_accounting_contract.md` stops naming Sub's local rate
  resolver as tax authority; Sub billing documents remain source facts while
  the composed tax owner supplies determination evidence.
- Tax writer/caller inventories become two-directional ratchets so fixed enums,
  local calculators and filing writers cannot return after retirement.
- `docs/PLATFORM_ADOPTION_LEDGER.md` records exact release, lineage, migration,
  shadow and cutover coordinates without claiming a tag from repository-local
  version text.
