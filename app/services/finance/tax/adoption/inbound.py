"""ERP source rows → `ERPSourceTaxFactV1` → public `dotmac_tax.TaxFact`.

One mapper per NAMED fact family, as
`docs/architecture/dotmac-tax-adoption-boundary.md` § "Typed ERP seams"
requires ("One mapper per named fact family translates that contract into
public `dotmac_tax.TaxFact`").  Each mapper is a pure function of already-loaded
ERP rows: no session, no query, no commit, and no decision about which tax
applies.

## The one-way rule

`dotmac_tax` imports nothing from ERP.  ERP touches `dotmac_tax` in exactly one
place — :func:`to_tax_fact` — and does so through a LAZY import of the public
package surface (`dotmac_tax.TaxFact`), never a submodule and never a model.
Everything else in this package is ERP-owned types, which is why the mappers can
be written, reviewed and unit-tested before the distribution is pinned.

:func:`to_tax_fact_kwargs` is the whole translation and it is pure: it returns
the exact keyword arguments `TaxFact(**kwargs)` is built from.  That keeps the
field-by-field mapping testable with the package absent, and
:data:`TAX_FACT_FIELDS` — ERP's mirror of the released contract's field list —
is checked against the real dataclass whenever the package IS installed.  A
contract that grows a required field therefore fails a test rather than a
production call.

## What a mapper deliberately refuses to do

**Choose a tax code.**  ERP's current calculator is handed `tax_code_ids` by its
caller (`app.services.finance.tax.tax_calculation`).  The module selects codes
from the fact signature plus effective party/supply/place classifications, and
that selection is fail-closed.  ERP's existing choices travel as
`observed_tax_code_refs` — evidence for the shadow comparator, never an input.

**Choose a classification category.**  `party_category`, `supply_category` and
`place_code` stay `None`.  They are evidenced, versioned, module-owned policy
(`TaxSubjectClassificationInput`), and an ERP mapper guessing one would be ERP
choosing a treatment under another name.  They remain settable only so a
documented operator-evidenced override can be passed through; no mapper here
populates them.

**Choose a rate, an inclusive flag or an order.**  None of the three appears on
`ERPSourceTaxFactV1` at all.  See the C1 note's "two VAT rows → one tax code"
section: it is precisely because a source fact CANNOT carry `is_inclusive` that
ERP's two VAT rows collapse into one module tax code with rule-level treatment.

**Invent a jurisdiction.**  `jurisdiction_id` is the MODULE's jurisdiction id
and is supplied by the caller from the C3 backfill map.  ERP's own
`tax.tax_jurisdiction.jurisdiction_id` is a different identifier space and is
never passed through.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.services.finance.money_boundary import MoneyBoundaryError, to_boundary_money
from app.services.finance.tax.adoption.contracts import (
    RECOGNITION_BASIS_ACCRUAL,
    RECOGNITION_BASIS_PAYROLL_PERIOD,
    ERPSourceTaxFactV1,
    SourceFactFamily,
    TaxAdapterRefusal,
    TransactionSide,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from dotmac_tax import TaxFact

    from app.models.finance.ap.supplier_invoice import SupplierInvoice
    from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
    from app.models.finance.ar.invoice import Invoice
    from app.models.finance.ar.invoice_line import InvoiceLine

__all__ = [
    "MAPPED_FAMILIES",
    "TAX_FACT_FIELDS",
    "UNMAPPED_FAMILIES",
    "ap_supplier_invoice_line_fact",
    "ar_invoice_line_fact",
    "payroll_taxable_pay_fact",
    "to_tax_fact",
    "to_tax_fact_kwargs",
]

_ZERO = Decimal("0")

#: The families C1 ships a mapper for.
MAPPED_FAMILIES: frozenset[SourceFactFamily] = frozenset(
    {
        SourceFactFamily.AR_INVOICE_LINE,
        SourceFactFamily.AP_INVOICE_LINE,
        SourceFactFamily.PAYROLL_TAXABLE_PAY,
    }
)

#: The families named as shadow cohorts but deliberately NOT mapped here.
#: Kept as members rather than deleted so the gap is visible and a caller that
#: reaches for one gets a refusal naming the cohort, not an AttributeError.
#: The C1 note records why each is out of scope.
UNMAPPED_FAMILIES: frozenset[SourceFactFamily] = (
    frozenset(SourceFactFamily) - MAPPED_FAMILIES
)

#: ERP's mirror of `dotmac_tax.TaxFact`'s field list at the version recorded in
#: `composition.CONTRACT_VERSION`.  A mirror, not an import: it must be
#: checkable with the distribution absent.  When the distribution IS installed,
#: `tests/ifrs/tax/test_tax_adoption_adapters.py` asserts the two agree exactly,
#: so a contract that adds a field fails a test instead of a production call.
TAX_FACT_FIELDS: frozenset[str] = frozenset(
    {
        "jurisdiction_id",
        "occurred_on",
        "fact_kind",
        "recognition_basis_code",
        "transaction_side",
        "base_amount",
        "source_ref",
        "source_version",
        "evidence_ref",
        "party_category",
        "supply_category",
        "place_code",
        "counterparty_ref",
        "supply_ref",
        "place_ref",
    }
)


def _require_uuid(value: object, label: str) -> UUID:
    if not isinstance(value, UUID):
        raise TaxAdapterRefusal(
            f"{label} must be a UUID, got {type(value).__name__}; ERP does not "
            "stringify an identity on the way to the module"
        )
    return value


def _require_date(value: object, label: str) -> date:
    if not isinstance(value, date):
        raise TaxAdapterRefusal(
            f"{label} must be a date, got {type(value).__name__}"
        )
    return value


def _require_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TaxAdapterRefusal(
            f"{label} must be an int, got {type(value).__name__}"
        )
    return value


def _base_money(amount: object, currency_code: object, *, field: str) -> Any:
    """Build exact kernel `Money`, translating a boundary refusal into ours.

    `to_boundary_money` already refuses floats, an unknown currency and excess
    minor-unit precision.  Re-raising as :class:`TaxAdapterRefusal` keeps one
    exception type at this seam so a caller has one thing to catch, while the
    original message (which names the offending value) is preserved.
    """
    if not isinstance(currency_code, str) or not currency_code.strip():
        raise TaxAdapterRefusal(f"{field}: a currency code is required")
    try:
        return to_boundary_money(amount, currency_code, field=field)
    except MoneyBoundaryError as exc:
        raise TaxAdapterRefusal(str(exc)) from exc


def _directed_base(
    line_amount: object,
    *,
    is_reversal_document: bool,
    document_type: str,
    field: str,
) -> tuple[Decimal, bool]:
    """Split an ERP signed line amount into (magnitude, reversal).

    `dotmac_tax.TaxFact.base_amount` must be non-negative and the module has no
    reversal concept: the MAGNITUDE is the fact, the DIRECTION is an ERP
    accounting consequence.

    The two document classes are treated differently ON PURPOSE:

    - A STANDARD/DEBIT_NOTE line must be non-negative.  ERP permits a negative
      DISCOUNT line inside such a document (`app/services/finance/ar/invoice.py`
      guards the invoice TOTAL, not the line: "Negative discount lines are fine
      as long as the net stays >= 0"), so a negative line here is a real,
      unresolved mapping — abs()-ing it would tax a discount as if it were a
      sale.  It is refused and recorded in the C1 note, not bent.
    - A CREDIT_NOTE is a reversal by DOCUMENT TYPE, which is a constrained
      column.  Its line sign convention is not constrained anywhere in ERP and
      both spellings occur, so the magnitude is taken and the direction comes
      from `invoice_type`.
    """
    if isinstance(line_amount, bool) or not isinstance(line_amount, Decimal):
        raise TaxAdapterRefusal(
            f"{field} must be a Decimal, got {type(line_amount).__name__}"
        )
    if is_reversal_document:
        return abs(line_amount), True
    if line_amount < _ZERO:
        raise TaxAdapterRefusal(
            f"{field} is {line_amount} on a {document_type} document. A "
            "negative line on a non-reversal document is an unresolved ERP "
            "mapping (a negative discount line is legal there); taking its "
            "magnitude would tax a discount as a supply. Refusing."
        )
    return line_amount, False


def ar_invoice_line_fact(
    invoice: Invoice,
    line: InvoiceLine,
    *,
    jurisdiction_id: UUID,
    recognition_basis_code: str = RECOGNITION_BASIS_ACCRUAL,
    place_ref: str | None = None,
    party_category: str | None = None,
    supply_category: str | None = None,
    place_code: str | None = None,
    correlation_ref: str | None = None,
) -> ERPSourceTaxFactV1:
    """Cohort 1: one AR invoice / credit-note line offered for output tax.

    Base is `InvoiceLine.line_amount` — the line's own extended amount, before
    any tax.  `InvoiceLine.tax_code_id` and the `ar.invoice_line_tax` rows hung
    off the line are the LEGACY OUTPUT of ERP's calculator, so they travel as
    `observed_tax_code_refs` for the shadow comparator and are not inputs.

    `PROFORMA` is refused: it is not an accounting document, it produces no tax
    consequence, and determining against it would burn a `source_version` on a
    document that may never exist.
    """
    from app.models.finance.ar.invoice import InvoiceType

    invoice_id = _require_uuid(
        getattr(invoice, "invoice_id", None), "invoice.invoice_id"
    )
    line_invoice_id = _require_uuid(
        getattr(line, "invoice_id", None), "line.invoice_id"
    )
    if line_invoice_id != invoice_id:
        raise TaxAdapterRefusal(
            f"line {getattr(line, 'line_id', None)} belongs to invoice "
            f"{line_invoice_id}, not {invoice_id}; refusing to build a fact "
            "from a mismatched document/line pair"
        )
    invoice_type = getattr(invoice, "invoice_type", None)
    if invoice_type == InvoiceType.PROFORMA:
        raise TaxAdapterRefusal(
            "a PROFORMA invoice is not an accounting document and has no tax "
            "consequence; refusing to consume a source version for it"
        )
    if invoice_type not in (
        InvoiceType.STANDARD,
        InvoiceType.DEBIT_NOTE,
        InvoiceType.CREDIT_NOTE,
    ):
        raise TaxAdapterRefusal(f"unknown AR invoice type {invoice_type!r}")

    amount, reversal = _directed_base(
        getattr(line, "line_amount", None),
        is_reversal_document=invoice_type == InvoiceType.CREDIT_NOTE,
        document_type=str(getattr(invoice_type, "value", invoice_type)),
        field="line.line_amount",
    )
    line_id = _require_uuid(getattr(line, "line_id", None), "line.line_id")
    item_id = getattr(line, "item_id", None)
    customer_id = _require_uuid(
        getattr(invoice, "customer_id", None), "invoice.customer_id"
    )
    return ERPSourceTaxFactV1(
        organization_id=_require_uuid(
            getattr(invoice, "organization_id", None), "invoice.organization_id"
        ),
        jurisdiction_id=_require_uuid(jurisdiction_id, "jurisdiction_id"),
        family=SourceFactFamily.AR_INVOICE_LINE,
        fact_kind=SourceFactFamily.AR_INVOICE_LINE.value,
        recognition_basis_code=recognition_basis_code,
        transaction_side=TransactionSide.OUTPUT,
        occurred_on=_require_date(
            getattr(invoice, "invoice_date", None), "invoice.invoice_date"
        ),
        base_amount=_base_money(
            amount,
            getattr(invoice, "currency_code", None),
            field="line.line_amount",
        ),
        source_ref=f"erp:ar.invoice_line:{line_id}",
        source_version=_document_version(invoice, "invoice.version"),
        evidence_ref=f"erp:ar.invoice:{invoice_id}",
        document_id=invoice_id,
        line_id=line_id,
        counterparty_ref=f"erp:customer:{customer_id}",
        supply_ref=None if item_id is None else f"erp:item:{item_id}",
        place_ref=place_ref,
        party_category=party_category,
        supply_category=supply_category,
        place_code=place_code,
        correlation_ref=correlation_ref,
        reversal=reversal,
        observed_tax_code_refs=_observed_line_tax_codes(line),
    )


def ap_supplier_invoice_line_fact(
    invoice: SupplierInvoice,
    line: SupplierInvoiceLine,
    *,
    jurisdiction_id: UUID,
    recognition_basis_code: str = RECOGNITION_BASIS_ACCRUAL,
    place_ref: str | None = None,
    party_category: str | None = None,
    supply_category: str | None = None,
    place_code: str | None = None,
    correlation_ref: str | None = None,
) -> ERPSourceTaxFactV1:
    """Cohort 2: one AP supplier-invoice / credit-note line offered for input tax.

    Identical in shape to the AR mapper and deliberately NOT shared with it: the
    two differ in transaction side, counterparty kind, document-type enum and
    evidence prefix, and a single parameterised mapper would hide exactly those
    four differences behind a flag.  Recoverability is not decided here — it is
    a module rule property (`TaxRuleInput.recoverable_rate`) that arrives back
    as the component's recoverable/non-recoverable split.
    """
    from app.models.finance.ap.supplier_invoice import SupplierInvoiceType

    invoice_id = _require_uuid(
        getattr(invoice, "invoice_id", None), "invoice.invoice_id"
    )
    line_invoice_id = _require_uuid(
        getattr(line, "invoice_id", None), "line.invoice_id"
    )
    if line_invoice_id != invoice_id:
        raise TaxAdapterRefusal(
            f"line {getattr(line, 'line_id', None)} belongs to supplier "
            f"invoice {line_invoice_id}, not {invoice_id}; refusing to build a "
            "fact from a mismatched document/line pair"
        )
    invoice_type = getattr(invoice, "invoice_type", None)
    if invoice_type not in (
        SupplierInvoiceType.STANDARD,
        SupplierInvoiceType.DEBIT_NOTE,
        SupplierInvoiceType.CREDIT_NOTE,
    ):
        raise TaxAdapterRefusal(f"unknown AP invoice type {invoice_type!r}")

    amount, reversal = _directed_base(
        getattr(line, "line_amount", None),
        is_reversal_document=invoice_type == SupplierInvoiceType.CREDIT_NOTE,
        document_type=str(getattr(invoice_type, "value", invoice_type)),
        field="line.line_amount",
    )
    line_id = _require_uuid(getattr(line, "line_id", None), "line.line_id")
    item_id = getattr(line, "item_id", None)
    supplier_id = _require_uuid(
        getattr(invoice, "supplier_id", None), "invoice.supplier_id"
    )
    return ERPSourceTaxFactV1(
        organization_id=_require_uuid(
            getattr(invoice, "organization_id", None), "invoice.organization_id"
        ),
        jurisdiction_id=_require_uuid(jurisdiction_id, "jurisdiction_id"),
        family=SourceFactFamily.AP_INVOICE_LINE,
        fact_kind=SourceFactFamily.AP_INVOICE_LINE.value,
        recognition_basis_code=recognition_basis_code,
        transaction_side=TransactionSide.INPUT,
        occurred_on=_require_date(
            getattr(invoice, "invoice_date", None), "invoice.invoice_date"
        ),
        base_amount=_base_money(
            amount,
            getattr(invoice, "currency_code", None),
            field="line.line_amount",
        ),
        source_ref=f"erp:ap.supplier_invoice_line:{line_id}",
        source_version=_document_version(invoice, "invoice.version"),
        evidence_ref=f"erp:ap.supplier_invoice:{invoice_id}",
        document_id=invoice_id,
        line_id=line_id,
        counterparty_ref=f"erp:supplier:{supplier_id}",
        supply_ref=None if item_id is None else f"erp:item:{item_id}",
        place_ref=place_ref,
        party_category=party_category,
        supply_category=supply_category,
        place_code=place_code,
        correlation_ref=correlation_ref,
        reversal=reversal,
        observed_tax_code_refs=_observed_line_tax_codes(line),
    )


def payroll_taxable_pay_fact(
    *,
    organization_id: UUID,
    jurisdiction_id: UUID,
    employee_id: UUID,
    slip_id: UUID,
    period_end: date,
    annual_taxable_income: Decimal,
    currency_code: str,
    source_version: str,
    payroll_entry_id: UUID | None = None,
    deduction_line_id: UUID | None = None,
    recognition_basis_code: str = RECOGNITION_BASIS_PAYROLL_PERIOD,
    party_category: str | None = None,
    supply_category: str | None = None,
    place_code: str | None = None,
    correlation_ref: str | None = None,
    observed_tax_code_refs: tuple[str, ...] = (),
) -> ERPSourceTaxFactV1:
    """Cohort 6: one employee's ANNUALISED taxable income for one payroll period.

    Keyword-only scalars rather than an ORM row, and that is the substantive
    design decision here.  The boundary document is explicit that payroll
    "retains taxable-base/relief facts" while the module "owns the progressive
    tax rule and determination", so the fact ERP owes the module is the TAXABLE
    BASE.  ERP does not store that base anywhere: `PAYECalculator.calculate`
    (`app/services/people/payroll/paye_calculator.py`) derives
    `taxable_income = annual_gross - total_statutory - rent_relief` in memory,
    and the only durable payroll artefact is a `payroll.salary_slip_deduction`
    row for component `PAYE` holding the resulting AMOUNT.  Re-deriving the base
    from a slip here would install a second payroll-base calculator inside the
    tax adapter — exactly the duplication this programme removes — so the
    payroll service computes it once and hands it over.

    The base is ANNUAL on purpose.  ERP's `payroll.tax_band` bounds
    (`min_amount`/`max_amount`) are annual thresholds and the calculator applies
    them to `annual_gross`-derived income, so a module progressive rule backfilled
    from those bands must receive the same annual base.  What ERP does AFTERWARDS
    to that annual answer — divide by twelve, then pro-rate by
    `payment_days / total_working_days` — is not expressible as a `TaxFact` and is
    NOT attempted here.  It stays an ERP consequence, and it is recorded in the
    C1 note as a mapping that does not round-trip.

    ERP's `payroll.tax_band` rows are likewise NOT read here: bands become module
    `TaxRuleBandInput` policy data at C3 backfill, and a determination that
    consulted ERP bands would be ERP calculating the tax again under another name.

    `transaction_side` is `liability`: employer-remitted employee tax is neither
    an input credit nor an output tax on a supply.  `period_end` is the
    occurrence date — a period-based tax occurs at the period boundary, not on
    `posting_date`, which can move for banking reasons without changing the tax.

    `source_version` is caller-supplied because `SalarySlip` carries no
    `VersionedMixin.version` (unlike both invoice headers).  The payroll writer
    owns the revision it publishes; this adapter will not invent one from a
    timestamp.
    """
    if isinstance(annual_taxable_income, bool) or not isinstance(
        annual_taxable_income, Decimal
    ):
        raise TaxAdapterRefusal(
            "annual taxable income must be a Decimal, got "
            f"{type(annual_taxable_income).__name__}"
        )
    if annual_taxable_income < _ZERO:
        raise TaxAdapterRefusal(
            f"annual taxable income is {annual_taxable_income}; a negative "
            "taxable base is a payroll correction, which has no reversal "
            "concept in the module and must be issued as an explicit "
            "adjustment run"
        )
    employee = _require_uuid(employee_id, "employee_id")
    slip = _require_uuid(slip_id, "slip_id")
    return ERPSourceTaxFactV1(
        organization_id=_require_uuid(organization_id, "organization_id"),
        jurisdiction_id=_require_uuid(jurisdiction_id, "jurisdiction_id"),
        family=SourceFactFamily.PAYROLL_TAXABLE_PAY,
        fact_kind=SourceFactFamily.PAYROLL_TAXABLE_PAY.value,
        recognition_basis_code=recognition_basis_code,
        transaction_side=TransactionSide.LIABILITY,
        occurred_on=_require_date(period_end, "period_end"),
        base_amount=_base_money(
            annual_taxable_income, currency_code, field="annual_taxable_income"
        ),
        source_ref=f"erp:payroll.salary_slip:{slip}:paye",
        source_version=source_version,
        evidence_ref=(
            f"erp:payroll.payroll_entry:{payroll_entry_id}"
            if payroll_entry_id is not None
            else f"erp:payroll.salary_slip:{slip}"
        ),
        document_id=slip,
        line_id=deduction_line_id,
        counterparty_ref=f"erp:employee:{employee}",
        supply_ref=None,
        place_ref=None,
        party_category=party_category,
        supply_category=supply_category,
        place_code=place_code,
        correlation_ref=correlation_ref,
        reversal=False,
        observed_tax_code_refs=observed_tax_code_refs,
    )


def _document_version(document: object, label: str) -> str:
    """ERP's optimistic-locking `version` as the module's source version.

    `VersionedMixin.version` (`app/models/mixins.py`) is the only monotonic
    revision either invoice header carries.  The module fingerprints each fact
    and refuses a reused `source_version` whose facts changed, so a caller that
    edits a document MUST advance the header version before resubmitting.

    A known, REPORTED gap: a line edit that does not bump the header version
    reuses a version with different facts.  The module then raises `TaxConflict`
    — loud and fail-closed, which is the correct end state — but the fix belongs
    in the AR/AP line writers at cutover, not in a wider version string invented
    here.  See the C1 note.
    """
    version = _require_int(getattr(document, "version", None), label)
    if version < 1:
        raise TaxAdapterRefusal(f"{label} must be >= 1, got {version}")
    return f"v{version}"


def _observed_line_tax_codes(line: object) -> tuple[str, ...]:
    """ERP's own calculator output, carried as shadow evidence only.

    Reads whatever `line_taxes` rows are ALREADY loaded on the line; it issues
    no query, so an unloaded relationship yields no evidence rather than an
    implicit lazy load inside an adapter.  Ordered by the legacy `sequence` and
    then the code id so the comparator sees a stable list — note that legacy
    ordering is not migration evidence (the boundary document is explicit that
    "Alphabetical order is not migration evidence"); this is the OBSERVED order,
    not a proposed one.
    """
    rows = line.__dict__.get("line_taxes")
    if not rows:
        return ()
    observed = sorted(
        (int(getattr(row, "sequence", 0) or 0), str(row.tax_code_id)) for row in rows
    )
    return tuple(f"erp:tax.tax_code:{code_id}" for _sequence, code_id in observed)


def to_tax_fact_kwargs(fact: ERPSourceTaxFactV1) -> dict[str, object]:
    """The exact keyword arguments `dotmac_tax.TaxFact` is built from.

    Pure, and separate from :func:`to_tax_fact` so the field-by-field mapping
    stays testable with the distribution absent.  Everything ERP-specific —
    `organization_id`, `family`, `document_id`, `line_id`, `reversal`,
    `correlation_ref`, `observed_tax_code_refs` — is DROPPED here: the module is
    given opaque refs and exact money, and nothing else.

    `tenant_id` is not among them either.  It is not a `TaxFact` field: the
    module takes it as a separate argument to `determine_tax_set`, and the
    caller passes `fact.tenant_id` (ERP's same-UUID `organization_id`).
    """
    if not isinstance(fact, ERPSourceTaxFactV1):
        raise TaxAdapterRefusal(
            f"expected an ERPSourceTaxFactV1, got {type(fact).__name__}"
        )
    if fact.family in UNMAPPED_FAMILIES:
        raise TaxAdapterRefusal(
            f"source fact family {fact.family.value!r} has no C1 mapper; it is "
            "a later shadow cohort. Build it deliberately rather than passing "
            "a hand-built fact through."
        )
    return {
        "jurisdiction_id": fact.jurisdiction_id,
        "occurred_on": fact.occurred_on,
        "fact_kind": fact.fact_kind,
        "recognition_basis_code": fact.recognition_basis_code,
        "transaction_side": fact.transaction_side.value,
        "base_amount": fact.base_amount,
        "source_ref": fact.source_ref,
        "source_version": fact.source_version,
        "evidence_ref": fact.evidence_ref,
        "party_category": fact.party_category,
        "supply_category": fact.supply_category,
        "place_code": fact.place_code,
        "counterparty_ref": fact.counterparty_ref,
        "supply_ref": fact.supply_ref,
        "place_ref": fact.place_ref,
    }


def to_tax_fact(fact: ERPSourceTaxFactV1) -> TaxFact:
    """The ONLY place in ERP that touches `dotmac_tax`.

    Lazily imported so that importing ERP's tax package does not require a
    distribution ERP has not pinned (see `composition`).  The import is of the
    package's PUBLIC surface — `from dotmac_tax import TaxFact` — never a
    submodule, so ERP depends on the released contract rather than on the
    module's internal layout.
    """
    try:
        from dotmac_tax import TaxFact as ModuleTaxFact
    except ImportError as exc:  # pragma: no cover - exercised only when unpinned
        raise TaxAdapterRefusal(
            "dotmac-tax is not installed; ERP has not pinned it. C1 delivers "
            "adapters only — see docs/architecture/"
            "dotmac-tax-adoption-boundary.md § 'Composition and release gates'."
        ) from exc
    return ModuleTaxFact(**to_tax_fact_kwargs(fact))  # type: ignore[arg-type]
