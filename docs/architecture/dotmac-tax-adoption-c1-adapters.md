# `dotmac-tax` C1 — typed source-fact and accounting-consequence adapters

Status: **C1 history, amended by C2 composition**
Companion to `docs/architecture/dotmac-tax-adoption-boundary.md`, which remains
the authoritative design. This note records what C1 built, what it deliberately
did not build, and every ERP field that does not map cleanly onto the published
`dotmac-tax` contract.

Original C1 contract: `dotmac-tax 0.1.0a2`. C2 now pins released
`dotmac-tax 0.1.0a3` (release run `32898397980`, peeled tag
`531f7f8c37ce2fdf41ecbf2f9a7a9940264a18f9`) and consumes its public
`TaxFact` and sealed `TaxDeterminationSetV1`/component/line contracts directly.
The `tx` lineage is composed at `tx_0003_result_fingerprint`; runtime authority
remains disabled.

Delivered:

| Artefact | Path |
|---|---|
| ERP-owned contracts | `app/services/finance/tax/adoption/contracts.py` |
| Inbound mappers | `app/services/finance/tax/adoption/inbound.py` |
| Outbound projection | `app/services/finance/tax/adoption/outbound.py` |
| Composition state (inert) | `app/services/finance/tax/adoption/composition.py` |
| Tests | `tests/ifrs/tax/test_tax_adoption_adapters.py` |

C1 changed no writer. C2 adds the exact dependency and module storage but still
repoints no calculator, reader or filing writer. Policy backfill, field-by-field
shadow comparison and cohort cutover remain C3/C4.

---

## 1. Source-fact families covered

| Family | Cohort | ERP source | Mapper |
|---|---|---|---|
| `ar_invoice_line` | 1 | `ar.invoice` + `ar.invoice_line` | `ar_invoice_line_fact` |
| `ap_invoice_line` | 2 | `ap.supplier_invoice` + `ap.supplier_invoice_line` | `ap_supplier_invoice_line_fact` |
| `payroll_taxable_pay` | 6 | `payroll.salary_slip` + `PAYECalculator` output | `payroll_taxable_pay_fact` |

These are the first two shadow cohorts plus payroll, which is the family whose
shape most stresses the contract (see § 5.7) and therefore the one worth proving
early rather than discovering at cutover.

## 2. Families deliberately NOT covered, and why

`SourceFactFamily` keeps a member for each so the gap is visible;
`to_tax_fact_kwargs` refuses an unmapped family by name rather than letting a
hand-built fact through (`UNMAPPED_FAMILIES`).

| Family / area | Why not in C1 |
|---|---|
| `ar_settlement_withholding`, `ap_settlement_withholding` (cohort 4) | Settlement WHT is not line-shaped. The fact is a gross settlement event spread across payment allocations, and the invariant that must survive is `net + WHT = gross` at the DOCUMENT level across partial settlements. A per-line mapper modelled on cohorts 1–2 would be the wrong shape, and ERP currently carries header deductions (`Invoice.withholding_tax_amount`, `vat_withheld`) that have no line to hang from. This needs its own fact shape and its own review. |
| `expense_entry` (cohort 5) | Depends on cohort 5's stamp-duty/fixed-levy decisions, which in turn depend on the module `fixed_amount` typed-money rule replacing ERP's `is_fixed_amount` + `tax_rate` overload. Mapping it before that adjudication would bake the overload in. |
| Recurring AR/AP generation (cohort 3) | Correctly has NO fact family of its own. `app/services/finance/automation/recurring.py` produces ordinary AR/AP lines and must submit the same typed line fact — the boundary document is explicit that no recurring-only calculator is retained. Adding a family here would create exactly the duplicate path it forbids. |
| Sub-imported accounting observations (cohort 7) | These are typed EXTERNAL observations that must be recorded, mapped and reconciled, with the source tax ids remaining evidence rather than ERP master ids. That is an importer contract, not a determination fact, and it belongs with the Sub sync boundary. |
| Deferred / current tax (cohort 6b) | The accounting measurement inputs stay in ERP by design; only where the fact IS a tax determination does module identity apply. Nothing here is a determination fact today. |
| Report boxes, filing obligations, return lifecycle (cohort 8) | Module-side authorities (`StatutoryReportBoxInput`, filing obligations, return transitions). They consume determinations; they are not source facts. |

## 3. Two ERP VAT rows → one module tax code

**This is the finding, not a footnote.**

ERP models the inclusive variant of VAT as a SEPARATE `tax.tax_code` row. Of the
six live codes in production, two are VAT and they differ in exactly one column:

| `tax_code` | `tax_type` | `tax_rate` | `is_inclusive` | everything else |
|---|---|---|---|---|
| `VAT-7.5` | `VAT` | `0.075000` | `false` | identical |
| `VAT-7.5 (inclusive)` | `VAT` | `0.075000` | `true` | identical |

That is the "variant as a new row" anti-pattern the whole programme exists to
remove. Its costs are the usual ones: the two rows drift independently (a rate
change must be applied twice, and nothing enforces that it is); return-box and
reporting-code identity is duplicated; `tax_collected_account_id` is mapped
twice; and every caller that must "pick the inclusive one" is making a POLICY
decision at a call site.

In `dotmac-tax`, `inclusive` is a column on a tax **rule**
(`TaxRuleInput.inclusive`), and a rule is selected by jurisdiction, effective
date, fact signature and the party/supply/place classifications. So:

```
ERP  tax.tax_code  "VAT-7.5"              ─┐
                                           ├─► ONE module TaxCode  (code "VAT", tax_kind_code "vat")
ERP  tax.tax_code  "VAT-7.5 (inclusive)"  ─┘        │
                                                    ├─► TaxRule v1  inclusive=false  rate=0.075  (exclusive supplies)
                                                    └─► TaxRule v1' inclusive=true   rate=0.075  (inclusive supplies)
```

Both rules carry the same `tax_code_id`, so return-box identity, reporting code
and the ERP account mapping exist **once**. What distinguishes them is the
supply/party/place classification that selects one over the other — which is
evidenced, versioned policy data, not a code a caller names.

Concretely, at backfill (C3):

| ERP evidence | Module projection |
|---|---|
| `VAT-7.5`.`tax_code_id`, `VAT-7.5 (inclusive)`.`tax_code_id` | both retained as ERP-side evidence refs; NEITHER becomes the module tax-code id |
| `tax_code` / `tax_name` / `tax_return_box` / `reporting_code` (identical on both rows) | one `TaxCode` |
| `tax_type = VAT` | open `tax_kind_code`, not an enum |
| `tax_rate = 0.075` (twice) | one rate, on two rules |
| `is_inclusive = false` | `TaxRule(inclusive=False, calculation_base_code="source_amount")` |
| `is_inclusive = true` | `TaxRule(inclusive=True)` — selected by the supply classification that says the price is tax-inclusive |
| `tax_collected_account_id` (twice) | ONE `TaxCodeAccounts` entry keyed by the module tax-code id |

**Every historical `ar.invoice_line_tax` / `ap.supplier_invoice_line_tax` row
pointing at either legacy id must be linked to the single new code and to the
rule whose `inclusive` matches the row's own `is_inclusive` snapshot.** A row
whose `is_inclusive` disagrees with the legacy code it references is an
`operator_adjudication_required` row, not a variance to normalise away.

The C1 adapters make the collapse structurally enforceable rather than merely
documented: **`ERPSourceTaxFactV1` has no `inclusive` field and no tax-code
field.** An ERP caller literally cannot request the inclusive variant. The rule
decides. `test_a_source_fact_cannot_express_inclusiveness_or_a_tax_code` asserts
both absences, and `to_tax_fact_kwargs` is checked to carry neither.

The same reasoning applies to the four remaining live codes:

| ERP rows | Module projection |
|---|---|
| `WHT 10%`, `WHT 5%` (both `WITHHOLDING`, compound, recoverable) | Separate rates are genuinely separate rules on ONE withholding code, selected by the **party/supply classification** that determines which rate a payee attracts — not by a caller passing a different `tax_code_id`. Whether they are one code or two is a C3 adjudication that must cite the legal basis for the rate split. |
| `WHT 2%` (`WITHHOLDING`, compound, NOT recoverable) | `recoverable_rate = 0` on its rule. `is_recoverable`/`recovery_rate` stop being code columns. |
| `SD-1%` (`STAMP_DUTY`, 0.01) | `tax_kind_code = "stamp_duty"` — open data, no enum migration. It is a percentage rule today; if any historical row used `is_fixed_amount`, that row needs the typed `fixed_amount` Money rule and is adjudicated separately (§ 5.2). |

`is_compound` alone does not survive the move: the module needs an explicit
positive `calculation_sequence` plus `calculation_base_code =
"source_plus_prior_tax"`. ERP's current ordering — ordinary taxes first, then
compound, then by tax-code **text** — is not migration evidence, and C3 must
record an approved ordering adjudication instead of copying it.

---

## 4. Field-by-field ERP → `TaxFact`

`TaxFact` has fifteen fields. `to_tax_fact_kwargs` produces exactly those and
nothing else; C2 checks the mapping keys directly against the installed public
dataclass, with no ERP-owned field-list mirror left to drift.

Note that `tenant_id` is **not** a `TaxFact` field: the module takes it as a
separate argument to `determine_tax_set`, and ERP passes `fact.tenant_id`, a
derived property returning `organization_id` (same-UUID mapping,
`app/tenancy.py`).

### 4.1 `ar_invoice_line` (transaction side `output`)

| `TaxFact` field | ERP source | Notes |
|---|---|---|
| `jurisdiction_id` | **caller-supplied** | The MODULE jurisdiction id from the C3 backfill map. ERP's `tax.tax_jurisdiction.jurisdiction_id` is a different identifier space and is never passed through. |
| `occurred_on` | `ar.invoice.invoice_date` | |
| `fact_kind` | constant `"ar_invoice_line"` | ERP declares this string; published rules must use the same `fact_kind`. |
| `recognition_basis_code` | `"accrual"` (default), `"cash"` | Matches ERP's own `TaxRecognitionBasis` vocabulary. Cash-basis VAT callers pass `"cash"` explicitly. |
| `transaction_side` | constant `"output"` | Crosses as the module's plain string, not an ERP enum. |
| `base_amount` | `ar.invoice_line.line_amount` + `ar.invoice.currency_code` | Exact kernel `Money` via `money_boundary.to_boundary_money`; magnitude only. |
| `source_ref` | `erp:ar.invoice_line:{line_id}` | The determination unit is the LINE. |
| `source_version` | **content digest** `cv2:<sha256>` over the tax-relevant fields | NOT `ar.invoice.version`. See § 5.3. |
| `evidence_ref` | `erp:ar.invoice:{invoice_id}` | |
| `counterparty_ref` | `erp:customer:{ar.invoice.customer_id}` | Opaque to the module. |
| `supply_ref` | `erp:item:{ar.invoice_line.item_id}` or `None` | `item_id` is nullable in ERP. |
| `place_ref` | **caller-supplied**, default `None` | See § 5.5. |
| `party_category` | never populated | Module-owned classification policy. |
| `supply_category` | never populated | Module-owned classification policy. |
| `place_code` | never populated | Module-owned classification policy. |

ERP-only fields carried on `ERPSourceTaxFactV1` and dropped before the module
sees them: `organization_id`, `family`, `document_id`, `line_id`, `reversal`,
`correlation_ref`, `observed_tax_code_refs`.

`observed_tax_code_refs` is the legacy `ar.invoice_line_tax` rows already loaded
on the line, rendered as `erp:tax.tax_code:{id}` and ordered by the legacy
`sequence`. It is **shadow-comparator evidence, never an input** — passing ERP's
chosen codes to the module would reinstate the decision ERP is retiring. The
mapper reads only an already-loaded relationship and issues no query.

### 4.2 `ap_invoice_line` (transaction side `input`)

Identical shape with four substitutions, and deliberately a separate function
rather than the AR mapper behind a flag:

| `TaxFact` field | ERP source |
|---|---|
| `transaction_side` | constant `"input"` |
| `base_amount` | `ap.supplier_invoice_line.line_amount` + `ap.supplier_invoice.currency_code` |
| `source_ref` | `erp:ap.supplier_invoice_line:{line_id}` |
| `source_version` | **content digest** `cv2:<sha256>`, identical scheme to AR |
| `evidence_ref` | `erp:ap.supplier_invoice:{invoice_id}` |
| `counterparty_ref` | `erp:supplier:{ap.supplier_invoice.supplier_id}` |

Recoverability is **not** an inbound field. `ap.supplier_invoice_line_tax
.is_recoverable` / `.recoverable_amount` are legacy calculator output; the module
decides recovery from `TaxRuleInput.recoverable_rate` and returns the split as
amounts.

### 4.3 `payroll_taxable_pay` (transaction side `liability`)

| `TaxFact` field | ERP source | Notes |
|---|---|---|
| `jurisdiction_id` | caller-supplied | as above |
| `occurred_on` | `payroll.salary_slip.end_date` | The period boundary, NOT `posting_date`, which moves for banking reasons without changing the tax. |
| `fact_kind` | constant `"payroll_taxable_pay"` | |
| `recognition_basis_code` | constant `"payroll_period"` | A third basis ERP declares; the module's vocabulary is open. |
| `transaction_side` | constant `"liability"` | Employer-remitted employee tax is neither an input credit nor an output tax on a supply. |
| `base_amount` | `PAYEBreakdown.taxable_income` (ANNUAL) | See § 5.7. |
| `source_ref` | `erp:payroll.salary_slip:{slip_id}:paye` | |
| `source_version` | **content digest** `cv2:<sha256>`, identical scheme to AR/AP | No caller parameter. See § 5.3. |
| `evidence_ref` | `erp:payroll.payroll_entry:{entry_id}` (falls back to the slip) | |
| `counterparty_ref` | `erp:employee:{employee_id}` | |
| `supply_ref`, `place_ref` | always `None` | Payroll has no supply or place. |
| `party_category`, `supply_category`, `place_code` | never populated | |

The mapper takes keyword scalars rather than an ORM row on purpose: the taxable
base is DERIVED in memory by `PAYECalculator.calculate`
(`annual_gross - total_statutory - rent_relief`) and is persisted nowhere. The
only durable payroll artefact is a `payroll.salary_slip_deduction` row for
component `PAYE` holding the resulting amount. Re-deriving the base inside the
tax adapter would install a second payroll-base calculator, which is the exact
duplication this programme removes.

`payroll.tax_band` rows are likewise not read: bands become module
`TaxRuleBandInput` policy at C3, and a determination that consulted ERP bands
would be ERP calculating the tax again under another name.

---

## 5. What does NOT map cleanly

Reported, not bent. Each item is either refused loudly by the adapter or flagged
here as a C3 adjudication.

### 5.1 Foreign-currency documents vs the jurisdiction currency — **blocker candidate**

`dotmac_tax` validates a fact's currency AND minor units against its
**jurisdiction's** currency (`_validate_tax_fact` → "tax fact uses the wrong
jurisdiction currency"). ERP's money boundary provisions both `NGN` and `USD`,
and both invoice headers carry `currency_code` plus `exchange_rate` /
`functional_currency_amount`, so ERP can hold a document whose currency is not
the tax jurisdiction's.

There is no field on `TaxFact` for an exchange rate or a functional amount, and
the adapter does not invent one. C2's outbound boundary structurally refuses a
result whose currency differs from ERP's declared functional currency, and C3
must keep such a row out of the module call entirely. Before cohort 1 shadowing,
C3 must
establish, from evidence, either that every in-scope document is in the
jurisdiction currency, or which converted amount is the legal tax base and who
owns that conversion. **This is an adjudication, not a defaulting decision, and
it is the item most likely to block cohorts 1–2.**

### 5.2 `is_fixed_amount` overloads `tax_rate`

`tax.tax_code.is_fixed_amount` reinterprets `tax_rate numeric(10,6)` as an
absolute money amount. The module has a typed `fixed_amount: Money` on the rule
and a separate `calculation_method = "fixed"`, so nothing is overloaded there.
The six live production codes are all percentage codes, so this is a historical-
row question: any row that ever used the overload must project to a fixed rule
with exact money and retain a source-row fingerprint proving the legacy
representation. `numeric(10,6)` also cannot hold a large fixed levy without
losing the integer part — that limit must be checked against actual values, not
assumed away.

### 5.3 `source_version` is a CONTENT DIGEST, not a row version — RESOLVED

An earlier revision of this note derived `source_version` from
`VersionedMixin.version` on the invoice header and claimed it was "the only
monotonic revision either invoice header carries". **That claim was wrong and
the approach was a defect.** `version` is an optimistic-locking counter whose
own docstring (`app/models/mixins.py`) says it "should be incremented on every
successful update" — a writer CONVENTION. There is no `server_onupdate`, no
trigger and no constraint, so nothing enforces monotonicity at all.

It fails in **both** directions, and only one of them is loud:

- **Under-count.** A line edit that does not bump the header reuses a version
  whose facts changed. The module fingerprints the fact and raises
  `TaxConflict`. Loud, fail-closed, survivable — this was the only direction
  originally reported.
- **Over-count — SILENT, and the serious one.** `version` bumps on ANY update:
  a status change, a memo, a posting flag. Each bump changes `source_version`
  while the tax-relevant facts are identical. The module's
  `uq_tax_determination_sets_source` is on
  `(tenant_id, source_ref, source_version)` and there is **no** uniqueness on
  `source_fingerprint`, so the new version matches no existing row and a SECOND
  determination set is created carrying an identical fingerprint. Duplicate
  statutory evidence for one unchanged fact, with nothing raising. That is the
  variant-as-a-new-row pattern of § 3 reproduced inside the determination
  evidence — the exact defect this programme exists to remove.

`inbound._content_source_version` now derives the version from CONTENT:
`cv2:<sha256>` over jurisdiction, `occurred_on`, `fact_kind`, recognition basis,
transaction side, exact base amount + currency + minor units, party/supply/place
refs, the three classification categories, `reversal`, `correlation_ref`, and
the sorted `observed_tax_code_refs`. Fields are length-prefixed
(`key:len:value`) so no
reference containing a delimiter can collide with a different field set; money
is digested as exact decimal text quantized to the currency's minor units and a
float is refused outright; `None` uses a sentinel no real reference can spell,
so an absent `supply_ref` and the literal string `"None"` cannot digest
identically.

C1 originally named this algorithm `cv1` and omitted `reversal`. C2 supersedes
it with `cv2` before any authority cutover: reversal controls every journal side
but is intentionally absent from the module fact, so projection now recomputes
the content version and refuses a source fact whose direction was changed after
submission. A later removal of shadow-only legacy code observations must use a
new namespace rather than silently changing `cv2`.

This closes both directions. An edit that changes a tax fact yields a new
version, correctly. An edit that does not yields the SAME version, so the
module's existing fingerprint check turns a resubmission into an idempotent
no-op instead of a duplicate set. It also removes the dependency on writer
discipline spread across every AR, AP and payroll writer — the C4 caveat the old
approach was really conceding.

One consequence worth stating plainly: because `observed_tax_code_refs` is
digested, a change in ERP's legacy calculator output alone produces a new
version and therefore a second determination set with the same tax answer. Those
two sets have DIFFERENT fingerprints and each records a genuinely different ERP
submission, so this is not the identical-fingerprint duplication above — it is
the shadow comparator's unit of comparison changing, which is what a shadow
cohort wants to see.

`cv2` namespaces the algorithm so a future, deliberate change to the field set or
the encoding is a NEW algorithm rather than a silent re-versioning of every fact
already determined under the old one.

### 5.4 Payroll needed no workaround after all — RESOLVED

`SalarySlip` does not use `VersionedMixin`, so the earlier design took
`source_version` as a caller-supplied parameter for payroll only. That asymmetry
existed solely to prop up the row-version scheme that § 5.3 retired. A content
digest works uniformly across AR, AP and payroll, so the parameter is gone and no
payroll caller has to invent a revision.

### 5.5 ERP has no place dimension

`TaxFact.place_ref` / `place_code` model a place of supply. ERP has no such
column on an invoice or a line — `billing_address` / `shipping_address` are JSONB
blobs, not references. `place_ref` is caller-supplied and defaults to `None`. If
any live rule needs a place classification, C3 must first create a real,
referenceable place dimension; a JSONB address is not one.

### 5.6 Negative lines and credit-note sign conventions

`TaxFact.base_amount` must be non-negative and the module has no reversal
concept, so the magnitude is the fact and the direction is an ERP consequence.
Two ERP realities do not map onto that cleanly:

- A **negative discount line inside a STANDARD invoice** is legal
  (`app/services/finance/ar/invoice.py` guards the invoice TOTAL, not the line).
  Taking its magnitude would tax a discount as if it were a supply, so the
  mapper **refuses** it and names the case. Cohort 1 must decide whether such
  lines are netted before determination or determined as negative adjustments —
  and the answer must be evidenced.
- A **credit note's line sign is unconstrained** anywhere in ERP; both spellings
  occur. Direction is therefore taken from `invoice_type`, which IS a constrained
  column, and the magnitude from the line. A credit note with mixed line signs is
  a document defect that this adapter cannot detect from one line.

### 5.7 Payroll annualisation, monthly division and pro-ration do not round-trip

ERP's PAYE path annualises pay, applies annual band thresholds, divides the
annual tax by twelve, and then pro-rates by `payment_days /
total_working_days`. A `TaxFact` can carry the annual taxable base — and does —
but there is no field for "divide by twelve" or "pro-rate by attendance", and
`determine_tax_set` returns the tax for the base it was given.

So the module's answer for a payroll fact is the **annual** PAYE, and the
monthly division plus attendance pro-ration remain an ERP consequence applied
afterwards. Cohort 6 shadowing must compare at the annual figure, not the payslip
figure, or it will report a difference on every part-month employee. Whether the
pro-rated monthly amount can be reconstructed exactly (rounding at each step)
is an open C3 question.

### 5.8 Output-side "recoverability" has no module equivalent

`ar.invoice_line_tax.is_recoverable` / `.recoverable_amount` exist "for special
cases like bad debt VAT relief" (the model's own comment). The module's
`recoverable_rate` is an input-tax recovery property on a rule. Bad-debt relief
is a later, separate event on an already-determined output tax, not a property
of the original determination, and C1 does not map it. Cohort 1 must confirm
whether any live AR row uses it.

### 5.9 Header-level deductions have no line to hang from

`Invoice.withholding_tax_amount`, `withholding_tax_code_id`,
`stamp_duty_amount`, `stamp_duty_code_id`, `stamp_duty_treatment` and
`vat_withheld` are HEADER columns computed from the invoice subtotal, not from a
line. They belong to cohorts 4–5 and have no C1 mapper (§ 2).

### 5.10 `recovery_rate numeric(5,4)` precision

ERP stores recovery as `numeric(5,4)`; the module's `recoverable_rate` uses its
own `RATE` type. C3 must verify no live value loses precision in projection
rather than assuming the scales agree.

---

## 6. The outbound projection

`project_determination_set(result, *, source_fact, application, accounts,
expected_fingerprint, fiscal_period_id) -> ConsequencePosting` is a **pure
function**. `result` is the released public `TaxDeterminationSetV1`;
`source_fact` is the exact ERP observation submitted for that result, and
`application` is ERP's separate posting context. The function verifies every
echoed source field before it
resolves accounts, and returns a typed consequence. A postable consequence
renders into `JournalInput`/`JournalLineInput` — the accounting owner's own
input type — while a reportable-only zero consequence renders no journal. It
performs no write; invoking
`BasePostingAdapter.create_approve_and_post_journal` and writing the document /
tax-transaction snapshot is C4.

Purity buys three things. It is testable with no database or session. It cannot
half-write, so "refuse the whole source row"
is structural rather than a `rollback()` someone has to remember. And the
preconditions it cannot check itself become REQUIRED ARGUMENTS: `fiscal_period_id`
is what `PeriodGuardService.require_open_period()` returns, so a caller that has
not proved the period open has nothing to pass; `expected_fingerprint` is what
ERP recorded when it submitted the fact, so a silently re-determined set cannot
be posted against a document priced on the previous one.

Which way the ignorance runs: the module produces amounts, code/rule identity
and treatment, and is never asked about an account, a journal, a period or a
side. Accounts enter through `TaxAccountMap` — an ERP-owned effective mapping
**keyed by module tax-code id**, deliberately not read from
`tax.tax_code.tax_collected_account_id` &c., because those columns belong to the
rows being replaced and their ids are a different identifier space.

Consequence shapes (`source_fact.reversal` flips every line):

| `AccountingConsequence` | Lines |
|---|---|
| `ar_output_tax` | CREDIT collected per component; DEBIT the receivable counterpart for the total |
| `ap_input_tax` | DEBIT recoverable → tax-paid (asset); DEBIT non-recoverable → tax-expense; CREDIT the payable counterpart. The one place a component yields two lines, and the reason the module's `recoverable_rate` must arrive as an AMOUNT split rather than a ratio ERP re-applies. |
| `payroll_tax_payable` | CREDIT collected (PAYE payable); DEBIT the payroll counterpart |

Generic `withholding` is not a C2 consequence. Customer-deducted WHT is a
receivable while supplier WHT is a payable; cohort 4 must add the typed
document-level fact and both account shapes before either becomes admissible.

Refusals: a result that disagrees with its exact submitted source fact, changed
fingerprint, attempted foreign-currency posting, missing or ambiguous account
mapping, a closed period (by construction), a currency mismatch, components out of calculation
order, a recovery split that does not total its component, components that do
not total the set, `net + tax != gross`, an inclusive set whose `source != gross`,
an exclusive set whose `source != net`, an inclusive component combined with any
other (mirroring the released a3 contract's own refusal), a transaction side
that contradicts ERP's accounting consequence, a standard-rated component
holding zero tax, and a projected posting that does not balance —
`JournalService._require_balanced` admits no tolerance, so it is refused here,
where the offending component can still be named.

Zero treatments are **carried, not dropped**. `zero_rated`, `exempt` and
`out_of_scope` are distinct treatments that all produce zero tax, and a zero
component is still reportable — it belongs in a return box. They land on
`ConsequencePosting.reportable_zero_components`. A set in which EVERY component
is a zero treatment is a normal reportable-only result: `is_postable` is false,
`lines` is empty, and `to_journal_input()` returns `None`. The caller can record
the distinct return-box components without catching a refusal, and no
meaningless zero-value journal or counterpart line is emitted.

Unlike the legacy `TAXPostingAdapter` — whose own docstring records that it emits
tax lines alone and leaves the contra to the source document — a
postable `ConsequencePosting` is self-balancing. A projection that cannot balance
itself can only be validated after being combined with something else, which is
exactly when a shadow comparison stops being able to attribute a difference.

Two ERP account mappings for one module tax code is an ambiguity, not
last-one-wins: it is refused at `TaxAccountMap` construction, per the boundary
document's "multiple plausible accounts" adjudication rule. A missing account is
never substituted with a suspense or a default.

---

## 7. The one-way import rule

`dotmac_tax` imports nothing from ERP. ERP imports only names from the package's
PUBLIC top-level surface, never module ORM models or internal submodules.
`inbound.to_tax_fact` builds the released `TaxFact`; `outbound.py` accepts the
released sealed result contracts. ERP owns only its source observation and
accounting-application context. `to_tax_fact_kwargs` remains a pure explicit
translation and is checked directly against the installed public dataclass;
there is no ERP field-list or result mirror left to drift.

---

## 8. Verification performed

The following is C1's historical verification record; it is not C2 evidence.

Ran:

- `ruff check` and `ruff format --check` at the exact version `poetry.lock` pins
  (0.15.0) over the adapter package and the test module — clean.
- `python3 -m py_compile` over all four adapter modules and the test module — clean.
- An offline logic harness that executes the real adapter code against a stubbed
  `dotmac_kernel.money`: 20/20 outbound behaviours, 16/16 inbound behaviours, and
  20/20 `source_version` digest behaviours as designed. This is a development aid,
  not test evidence.

NOT run here (must be green in CI before this is merged):

- `pytest tests/ifrs/tax/test_tax_adoption_adapters.py` and the architecture suites;
- `mypy`.

CI on the final SHA is the acceptance evidence.

## 9. What C1 did not do

Historically, C1 added no dependency, lineage, backfill, shadow run or writer
switch. C2 now supplies the exact a3 artifact and storage lineage and deletes
the temporary result mirror. It still performs no backfill, shadow comparison,
module determination call or writer switch. The next gated step is C3.
