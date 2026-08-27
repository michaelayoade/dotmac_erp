"""ERP source rows → `ERPSourceTaxFactV1` → public `dotmac_tax.TaxFact`.

One mapper per NAMED fact family, as
`docs/architecture/dotmac-tax-adoption-boundary.md` § "Typed ERP seams"
requires ("One mapper per named fact family translates that contract into
public `dotmac_tax.TaxFact`").  Each mapper is a pure function of already-loaded
ERP rows: no session, no query, no commit, and no decision about which tax
applies.

## The one-way rule

`dotmac_tax` imports nothing from ERP. ERP imports only the released package's
public surface (`dotmac_tax.TaxFact`), never a submodule or model. The source
mappers themselves still produce ERP-owned value objects; :func:`to_tax_fact`
is the single translation into the module contract.

:func:`to_tax_fact_kwargs` is the whole translation and it is pure: it returns
the exact keyword arguments `TaxFact(**kwargs)` is built from. C2 pins the
released package, so the adapter validates against that public dataclass
directly rather than maintaining a second field-list mirror.

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

import hashlib
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING
from uuid import UUID

from dotmac_kernel.money import Money
from dotmac_tax import TaxFact

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
    from app.models.finance.ap.supplier_invoice import SupplierInvoice
    from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
    from app.models.finance.ar.invoice import Invoice
    from app.models.finance.ar.invoice_line import InvoiceLine

__all__ = [
    "MAPPED_FAMILIES",
    "UNMAPPED_FAMILIES",
    "ap_supplier_invoice_line_fact",
    "ar_invoice_line_fact",
    "payroll_taxable_pay_fact",
    "source_fact_content_version",
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


def _require_uuid(value: object, label: str) -> UUID:
    if not isinstance(value, UUID):
        raise TaxAdapterRefusal(
            f"{label} must be a UUID, got {type(value).__name__}; ERP does not "
            "stringify an identity on the way to the module"
        )
    return value


def _require_date(value: object, label: str) -> date:
    if not isinstance(value, date):
        raise TaxAdapterRefusal(f"{label} must be a date, got {type(value).__name__}")
    return value


def _base_money(amount: object, currency_code: object, *, field: str) -> Money:
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
    occurred_on = _require_date(
        getattr(invoice, "invoice_date", None), "invoice.invoice_date"
    )
    base_amount = _base_money(
        amount, getattr(invoice, "currency_code", None), field="line.line_amount"
    )
    counterparty_ref = f"erp:customer:{customer_id}"
    supply_ref = None if item_id is None else f"erp:item:{item_id}"
    observed_tax_code_refs = _observed_line_tax_codes(line)
    return ERPSourceTaxFactV1(
        organization_id=_require_uuid(
            getattr(invoice, "organization_id", None), "invoice.organization_id"
        ),
        jurisdiction_id=_require_uuid(jurisdiction_id, "jurisdiction_id"),
        family=SourceFactFamily.AR_INVOICE_LINE,
        fact_kind=SourceFactFamily.AR_INVOICE_LINE.value,
        recognition_basis_code=recognition_basis_code,
        transaction_side=TransactionSide.OUTPUT,
        occurred_on=occurred_on,
        base_amount=base_amount,
        source_ref=f"erp:ar.invoice_line:{line_id}",
        source_version=_content_source_version(
            jurisdiction_id=jurisdiction_id,
            occurred_on=occurred_on,
            fact_kind=SourceFactFamily.AR_INVOICE_LINE.value,
            recognition_basis_code=recognition_basis_code,
            transaction_side=TransactionSide.OUTPUT,
            base_amount=base_amount,
            counterparty_ref=counterparty_ref,
            supply_ref=supply_ref,
            place_ref=place_ref,
            party_category=party_category,
            supply_category=supply_category,
            place_code=place_code,
            reversal=reversal,
            correlation_ref=correlation_ref,
            observed_tax_code_refs=observed_tax_code_refs,
        ),
        evidence_ref=f"erp:ar.invoice:{invoice_id}",
        document_id=invoice_id,
        line_id=line_id,
        counterparty_ref=counterparty_ref,
        supply_ref=supply_ref,
        place_ref=place_ref,
        party_category=party_category,
        supply_category=supply_category,
        place_code=place_code,
        correlation_ref=correlation_ref,
        reversal=reversal,
        observed_tax_code_refs=observed_tax_code_refs,
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
    occurred_on = _require_date(
        getattr(invoice, "invoice_date", None), "invoice.invoice_date"
    )
    base_amount = _base_money(
        amount, getattr(invoice, "currency_code", None), field="line.line_amount"
    )
    counterparty_ref = f"erp:supplier:{supplier_id}"
    supply_ref = None if item_id is None else f"erp:item:{item_id}"
    observed_tax_code_refs = _observed_line_tax_codes(line)
    return ERPSourceTaxFactV1(
        organization_id=_require_uuid(
            getattr(invoice, "organization_id", None), "invoice.organization_id"
        ),
        jurisdiction_id=_require_uuid(jurisdiction_id, "jurisdiction_id"),
        family=SourceFactFamily.AP_INVOICE_LINE,
        fact_kind=SourceFactFamily.AP_INVOICE_LINE.value,
        recognition_basis_code=recognition_basis_code,
        transaction_side=TransactionSide.INPUT,
        occurred_on=occurred_on,
        base_amount=base_amount,
        source_ref=f"erp:ap.supplier_invoice_line:{line_id}",
        source_version=_content_source_version(
            jurisdiction_id=jurisdiction_id,
            occurred_on=occurred_on,
            fact_kind=SourceFactFamily.AP_INVOICE_LINE.value,
            recognition_basis_code=recognition_basis_code,
            transaction_side=TransactionSide.INPUT,
            base_amount=base_amount,
            counterparty_ref=counterparty_ref,
            supply_ref=supply_ref,
            place_ref=place_ref,
            party_category=party_category,
            supply_category=supply_category,
            place_code=place_code,
            reversal=reversal,
            correlation_ref=correlation_ref,
            observed_tax_code_refs=observed_tax_code_refs,
        ),
        evidence_ref=f"erp:ap.supplier_invoice:{invoice_id}",
        document_id=invoice_id,
        line_id=line_id,
        counterparty_ref=counterparty_ref,
        supply_ref=supply_ref,
        place_ref=place_ref,
        party_category=party_category,
        supply_category=supply_category,
        place_code=place_code,
        correlation_ref=correlation_ref,
        reversal=reversal,
        observed_tax_code_refs=observed_tax_code_refs,
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

    `source_version` is a CONTENT DIGEST, exactly as for AR and AP.  There was
    briefly a `source_version` parameter here because `SalarySlip` carries no
    `VersionedMixin.version` to borrow — but that workaround only existed to prop
    up a row-version scheme that was wrong for the invoice families too (see
    :func:`_content_source_version`).  A digest works uniformly across all three
    families, so the parameter is gone and no payroll caller has to invent a
    revision.
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
    occurred_on = _require_date(period_end, "period_end")
    base_amount = _base_money(
        annual_taxable_income, currency_code, field="annual_taxable_income"
    )
    counterparty_ref = f"erp:employee:{employee}"
    observed_tax_code_refs = tuple(observed_tax_code_refs)
    return ERPSourceTaxFactV1(
        organization_id=_require_uuid(organization_id, "organization_id"),
        jurisdiction_id=_require_uuid(jurisdiction_id, "jurisdiction_id"),
        family=SourceFactFamily.PAYROLL_TAXABLE_PAY,
        fact_kind=SourceFactFamily.PAYROLL_TAXABLE_PAY.value,
        recognition_basis_code=recognition_basis_code,
        transaction_side=TransactionSide.LIABILITY,
        occurred_on=occurred_on,
        base_amount=base_amount,
        source_ref=f"erp:payroll.salary_slip:{slip}:paye",
        source_version=_content_source_version(
            jurisdiction_id=jurisdiction_id,
            occurred_on=occurred_on,
            fact_kind=SourceFactFamily.PAYROLL_TAXABLE_PAY.value,
            recognition_basis_code=recognition_basis_code,
            transaction_side=TransactionSide.LIABILITY,
            base_amount=base_amount,
            counterparty_ref=counterparty_ref,
            supply_ref=None,
            place_ref=None,
            party_category=party_category,
            supply_category=supply_category,
            place_code=place_code,
            reversal=False,
            correlation_ref=correlation_ref,
            observed_tax_code_refs=observed_tax_code_refs,
        ),
        evidence_ref=(
            f"erp:payroll.payroll_entry:{payroll_entry_id}"
            if payroll_entry_id is not None
            else f"erp:payroll.salary_slip:{slip}"
        ),
        document_id=slip,
        line_id=deduction_line_id,
        counterparty_ref=counterparty_ref,
        supply_ref=None,
        place_ref=None,
        party_category=party_category,
        supply_category=supply_category,
        place_code=place_code,
        correlation_ref=correlation_ref,
        reversal=False,
        observed_tax_code_refs=observed_tax_code_refs,
    )


def _content_source_version(
    *,
    jurisdiction_id: UUID,
    occurred_on: date,
    fact_kind: str,
    recognition_basis_code: str,
    transaction_side: TransactionSide,
    base_amount: Money,
    counterparty_ref: str | None,
    supply_ref: str | None,
    place_ref: str | None,
    party_category: str | None,
    supply_category: str | None,
    place_code: str | None,
    reversal: bool,
    correlation_ref: str | None,
    observed_tax_code_refs: tuple[str, ...],
) -> str:
    """A stable digest of the tax-relevant CONTENT of one ERP source row.

    ## Why this is not a row's `version` column

    The obvious candidate — `VersionedMixin.version` on either invoice header —
    is an OPTIMISTIC-LOCKING counter, not a content revision.  Its own docstring
    (`app/models/mixins.py`) says it "should be incremented on every successful
    update": a writer CONVENTION, with no `server_onupdate`, no trigger and no
    constraint enforcing it.  Using it fails in both directions, and only one of
    them is loud:

    - **Under-count.**  A line edit that does not bump the header reuses a
      version whose facts changed.  The module fingerprints the fact and raises
      `TaxConflict`.  Loud, fail-closed, survivable.
    - **Over-count, and this one is SILENT.**  `version` bumps on ANY update — a
      status change, a memo, a posting flag — while the tax-relevant facts are
      identical.  `uq_tax_determination_sets_source` is on
      `(tenant_id, source_ref, source_version)` and there is NO uniqueness on
      `source_fingerprint`, so the new version matches no existing row and a
      SECOND determination set is created carrying an identical fingerprint.
      Duplicate statutory evidence for one unchanged fact, with nothing raising.
      That is the variant-as-a-new-row pattern reproduced inside the
      determination evidence — precisely what this programme exists to remove.

    Deriving the version from CONTENT closes both.  An edit that changes a tax
    fact yields a new version, correctly.  An edit that does not yields the SAME
    version, so the module's existing fingerprint check turns a resubmission
    into an idempotent no-op instead of a duplicate set.  It also stops the
    correctness of statutory evidence depending on writer discipline spread
    across every AR, AP and payroll writer.

    ## What goes in, and what deliberately does not

    Everything the module is given, plus `observed_tax_code_refs` — ERP's own
    legacy calculator output, which is part of what makes a submitted ERP row
    distinct during the shadow cohorts.  Note the consequence: a legacy-output
    change alone produces a new version and therefore a second determination set
    with the same tax answer.  Those two sets have DIFFERENT fingerprints and
    each records a genuinely different ERP submission, so this is not the
    identical-fingerprint duplication described above; it is the shadow
    comparator's unit of comparison changing.

    `source_ref` is NOT digested: this is the version OF a source ref, and
    including it would only make the digest opaque. `evidence_ref`,
    `document_id` and `line_id` are identity rather than content and are fixed
    for a given `source_ref`. `reversal` and `correlation_ref` ARE digested
    because they control the ERP consequence even though the module does not
    carry them; omitting reversal allowed one determination to be replayed with
    the opposite accounting direction.

    ## Encoding

    Length-prefixed `key:len:value` fields, so no reference containing a
    delimiter can be made to collide with a different field set.  Money is
    digested as EXACT text quantized to its currency's minor units, never a
    float and never a repr.  `observed_tax_code_refs` is SORTED, because the
    underlying `line_taxes` collection has no meaningful order for this purpose
    and an ordering change is not a content change.
    """
    parts: list[tuple[str, str]] = [
        ("jurisdiction_id", str(jurisdiction_id)),
        ("occurred_on", occurred_on.isoformat()),
        ("fact_kind", fact_kind),
        ("recognition_basis_code", recognition_basis_code),
        ("transaction_side", transaction_side.value),
        ("base_amount", _exact_money_text(base_amount)),
        ("currency_code", base_amount.currency.code),
        ("minor_units", str(base_amount.currency.minor_units)),
        ("counterparty_ref", _digest_optional(counterparty_ref)),
        ("supply_ref", _digest_optional(supply_ref)),
        ("place_ref", _digest_optional(place_ref)),
        ("party_category", _digest_optional(party_category)),
        ("supply_category", _digest_optional(supply_category)),
        ("place_code", _digest_optional(place_code)),
        ("reversal", "1" if reversal else "0"),
        ("correlation_ref", _digest_optional(correlation_ref)),
        ("observed_tax_code_refs", "\x1f".join(sorted(observed_tax_code_refs))),
    ]
    payload = "\n".join(f"{key}:{len(value)}:{value}" for key, value in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{_SOURCE_VERSION_ALGORITHM}:{digest}"


#: Namespaces the digest so a future, deliberate change to the field set or the
#: encoding is a NEW algorithm rather than a silent re-versioning of every fact
#: already determined under the old one.
_SOURCE_VERSION_ALGORITHM = "cv2"


def source_fact_content_version(fact: ERPSourceTaxFactV1) -> str:
    """Recompute the version of an already-built ERP source fact.

    Projection calls this before using ERP-only direction or document fields,
    so changing one of those fields without rebuilding the submitted fact is a
    refusal rather than a differently directed journal.
    """
    if not isinstance(fact, ERPSourceTaxFactV1):
        raise TaxAdapterRefusal(
            f"expected an ERPSourceTaxFactV1, got {type(fact).__name__}"
        )
    return _content_source_version(
        jurisdiction_id=fact.jurisdiction_id,
        occurred_on=fact.occurred_on,
        fact_kind=fact.fact_kind,
        recognition_basis_code=fact.recognition_basis_code,
        transaction_side=fact.transaction_side,
        base_amount=fact.base_amount,
        counterparty_ref=fact.counterparty_ref,
        supply_ref=fact.supply_ref,
        place_ref=fact.place_ref,
        party_category=fact.party_category,
        supply_category=fact.supply_category,
        place_code=fact.place_code,
        reversal=fact.reversal,
        correlation_ref=fact.correlation_ref,
        observed_tax_code_refs=fact.observed_tax_code_refs,
    )


#: A sentinel for `None` that no real reference can spell, so an absent
#: `supply_ref` and the literal string "None" cannot digest identically.
_ABSENT = "\x00absent"


def _digest_optional(value: str | None) -> str:
    return _ABSENT if value is None else value


def _exact_money_text(money: Money) -> str:
    """Exact decimal text for an amount, refusing anything inexact.

    `Money` built through `money_boundary` already holds a `Decimal` quantized
    to its currency's minor units, so the quantize below is a no-op that makes
    the canonical form guaranteed rather than assumed: `Decimal("100.5")` and
    `Decimal("100.50")` are numerically equal and must not digest differently.
    A float amount is refused outright — digesting `repr(float)` would make the
    version depend on binary rounding.
    """
    amount = money.amount
    if isinstance(amount, bool) or not isinstance(amount, Decimal):
        raise TaxAdapterRefusal(
            f"a source version cannot be derived from {type(amount).__name__} "
            f"{amount!r}; money must be an exact Decimal"
        )
    if not amount.is_finite():
        raise TaxAdapterRefusal(f"non-finite amount {amount!r} is not money")
    try:
        canonical = amount.quantize(Decimal(1).scaleb(-money.currency.minor_units))
    except (ArithmeticError, InvalidOperation) as exc:
        raise TaxAdapterRefusal(
            f"amount {amount!r} cannot be expressed exactly in "
            f"{money.currency.code} minor units"
        ) from exc
    return format(canonical, "f")


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
    """Build the released public module fact without importing internals."""

    return TaxFact(**to_tax_fact_kwargs(fact))  # type: ignore[arg-type]
