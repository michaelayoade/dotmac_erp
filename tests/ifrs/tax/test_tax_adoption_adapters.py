"""C2: ERP source facts and public `dotmac-tax` results at the real seams.

Every test here builds the SAME value objects a production caller builds —
`ERPSourceTaxFactV1` via the named family mappers and the released public
`TaxDeterminationSetV1` via its constructor — rather than poking at private
helpers. ERP adds a separate `TaxApplicationContextV1`; it never mirrors the
module's result.

The amounts are the ones ERP actually runs on in production: VAT 7.5 %, WHT 2 %
compounding on top of it, and a 1 % stamp duty.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from dotmac_tax import (
    TaxDeterminationComponentV1,
    TaxDeterminationLineV1,
    TaxDeterminationSetV1,
    TaxFact,
)

from app.models.finance.ap.supplier_invoice import SupplierInvoice, SupplierInvoiceType
from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
from app.models.finance.ap.supplier_invoice_line_tax import SupplierInvoiceLineTax
from app.models.finance.ar.invoice import Invoice, InvoiceType
from app.models.finance.ar.invoice_line import InvoiceLine
from app.models.finance.ar.invoice_line_tax import InvoiceLineTax
from app.services.finance.money_boundary import to_boundary_money
from app.services.finance.tax.adoption import (
    MAPPED_FAMILIES,
    UNMAPPED_FAMILIES,
    AccountingConsequence,
    ConsequencePosting,
    ERPSourceTaxFactV1,
    SourceFactFamily,
    TaxAccountMap,
    TaxApplicationContextV1,
    TaxAdapterRefusal,
    TransactionSide,
    ap_supplier_invoice_line_fact,
    ar_invoice_line_fact,
    payroll_taxable_pay_fact,
    project_determination_set,
    source_fact_content_version,
    to_tax_fact_kwargs,
)
from app.services.finance.tax.adoption.outbound import SOURCE_MODULE, TaxCodeAccounts

NGN = "NGN"
JURISDICTION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
ORG_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")

VAT_CODE_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
WHT_2_CODE_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")
STAMP_DUTY_CODE_ID = uuid.UUID("55555555-5555-4555-8555-555555555555")
PAYE_CODE_ID = uuid.UUID("99999999-9999-4999-8999-999999999999")

VAT_COLLECTED_ACCOUNT = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
VAT_PAID_ACCOUNT = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000002")
WHT_EXPENSE_ACCOUNT = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000003")
WHT_COLLECTED_ACCOUNT = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000004")
COUNTERPART_ACCOUNT = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000009")
PAYE_PAYABLE_ACCOUNT = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000010")
FISCAL_PERIOD_ID = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000001")
DETERMINATION_SET_ID = uuid.UUID("cccccccc-0000-4000-8000-000000000001")
DOCUMENT_ID = uuid.UUID("dddddddd-0000-4000-8000-000000000001")
LINE_ID = uuid.UUID("eeeeeeee-0000-4000-8000-000000000001")

FINGERPRINT = "f" * 64
RESULT_FINGERPRINT = "rv1:" + "a" * 64


def ngn(amount: str):
    return to_boundary_money(Decimal(amount), NGN)


# ---------------------------------------------------------------- AR / AP rows


def build_ar_invoice(
    *,
    invoice_type: InvoiceType = InvoiceType.STANDARD,
    invoice_id: uuid.UUID | None = None,
    version: int = 3,
    currency_code: str = NGN,
    invoice_date: date = date(2026, 3, 31),
) -> Invoice:
    return Invoice(
        invoice_id=invoice_id or uuid.uuid4(),
        organization_id=ORG_ID,
        customer_id=uuid.UUID("66666666-6666-4666-8666-666666666666"),
        invoice_number="INV-0001",
        invoice_type=invoice_type,
        invoice_date=invoice_date,
        due_date=date(2026, 4, 30),
        currency_code=currency_code,
        version=version,
    )


def build_ar_line(
    invoice: Invoice,
    *,
    line_amount: Decimal = Decimal("100000.00"),
    line_id: uuid.UUID | None = None,
    item_id: uuid.UUID | None = None,
    invoice_id: uuid.UUID | None = None,
) -> InvoiceLine:
    return InvoiceLine(
        line_id=line_id or uuid.uuid4(),
        invoice_id=invoice_id or invoice.invoice_id,
        line_number=1,
        description="Fibre link, March 2026",
        quantity=Decimal("1"),
        unit_price=line_amount,
        line_amount=line_amount,
        item_id=item_id,
    )


def build_ap_invoice(
    *,
    invoice_type: SupplierInvoiceType = SupplierInvoiceType.STANDARD,
    version: int = 2,
) -> SupplierInvoice:
    return SupplierInvoice(
        invoice_id=uuid.uuid4(),
        organization_id=ORG_ID,
        supplier_id=uuid.UUID("77777777-7777-4777-8777-777777777777"),
        invoice_number="AP-0001",
        invoice_type=invoice_type,
        invoice_date=date(2026, 3, 20),
        received_date=date(2026, 3, 21),
        due_date=date(2026, 4, 20),
        currency_code=NGN,
        version=version,
    )


def build_ap_line(
    invoice: SupplierInvoice,
    *,
    line_amount: Decimal = Decimal("100000.00"),
) -> SupplierInvoiceLine:
    return SupplierInvoiceLine(
        line_id=uuid.uuid4(),
        invoice_id=invoice.invoice_id,
        line_number=1,
        description="Transit bandwidth",
        quantity=Decimal("1"),
        unit_price=line_amount,
        line_amount=line_amount,
    )


# ================================================================== INBOUND: AR


def test_ar_invoice_line_fact_maps_every_field():
    invoice = build_ar_invoice()
    item_id = uuid.uuid4()
    line = build_ar_line(invoice, item_id=item_id)

    fact = ar_invoice_line_fact(invoice, line, jurisdiction_id=JURISDICTION_ID)

    assert fact.organization_id == ORG_ID
    assert fact.tenant_id == ORG_ID, "same-UUID mapping is derived, never stored twice"
    assert fact.jurisdiction_id == JURISDICTION_ID
    assert fact.family is SourceFactFamily.AR_INVOICE_LINE
    assert fact.fact_kind == "ar_invoice_line"
    assert fact.recognition_basis_code == "accrual"
    assert fact.transaction_side is TransactionSide.OUTPUT
    assert fact.occurred_on == date(2026, 3, 31)
    assert fact.base_amount == ngn("100000.00")
    assert fact.currency_code == NGN
    assert fact.minor_units == 2
    assert fact.source_ref == f"erp:ar.invoice_line:{line.line_id}"
    assert fact.source_version.startswith("cv2:")
    assert len(fact.source_version) == len("cv2:") + 64
    assert fact.evidence_ref == f"erp:ar.invoice:{invoice.invoice_id}"
    assert fact.document_id == invoice.invoice_id
    assert fact.line_id == line.line_id
    assert fact.counterparty_ref == f"erp:customer:{invoice.customer_id}"
    assert fact.supply_ref == f"erp:item:{item_id}"
    assert fact.reversal is False


def test_ar_mapper_never_chooses_a_classification_category():
    """ERP supplies refs; the module resolves categories from its own policy."""
    invoice = build_ar_invoice()
    fact = ar_invoice_line_fact(
        invoice, build_ar_line(invoice), jurisdiction_id=JURISDICTION_ID
    )
    assert fact.party_category is None
    assert fact.supply_category is None
    assert fact.place_code is None


def test_ar_credit_note_carries_a_positive_base_and_a_reversal_flag():
    invoice = build_ar_invoice(invoice_type=InvoiceType.CREDIT_NOTE)
    line = build_ar_line(invoice, line_amount=Decimal("-40000.00"))

    fact = ar_invoice_line_fact(invoice, line, jurisdiction_id=JURISDICTION_ID)

    assert fact.base_amount == ngn("40000.00")
    assert fact.reversal is True


def test_negative_line_on_a_standard_invoice_is_refused_not_absolutised():
    """A negative discount line is legal in ERP and is NOT a reversal."""
    invoice = build_ar_invoice()
    line = build_ar_line(invoice, line_amount=Decimal("-5000.00"))

    with pytest.raises(TaxAdapterRefusal, match="negative line"):
        ar_invoice_line_fact(invoice, line, jurisdiction_id=JURISDICTION_ID)


def test_proforma_never_consumes_a_source_version():
    invoice = build_ar_invoice(invoice_type=InvoiceType.PROFORMA)
    with pytest.raises(TaxAdapterRefusal, match="PROFORMA"):
        ar_invoice_line_fact(
            invoice, build_ar_line(invoice), jurisdiction_id=JURISDICTION_ID
        )


def test_a_line_from_another_invoice_is_refused():
    invoice = build_ar_invoice()
    stray = build_ar_line(invoice, invoice_id=uuid.uuid4())
    with pytest.raises(TaxAdapterRefusal, match="mismatched document/line pair"):
        ar_invoice_line_fact(invoice, stray, jurisdiction_id=JURISDICTION_ID)


def test_legacy_line_taxes_travel_as_observed_evidence_only():
    invoice = build_ar_invoice()
    line = build_ar_line(invoice)
    line.line_taxes = [
        InvoiceLineTax(
            line_tax_id=uuid.uuid4(),
            line_id=line.line_id,
            tax_code_id=WHT_2_CODE_ID,
            base_amount=Decimal("107500.00"),
            tax_rate=Decimal("0.020000"),
            tax_amount=Decimal("2150.00"),
            sequence=2,
        ),
        InvoiceLineTax(
            line_tax_id=uuid.uuid4(),
            line_id=line.line_id,
            tax_code_id=VAT_CODE_ID,
            base_amount=Decimal("100000.00"),
            tax_rate=Decimal("0.075000"),
            tax_amount=Decimal("7500.00"),
            sequence=1,
        ),
    ]

    fact = ar_invoice_line_fact(invoice, line, jurisdiction_id=JURISDICTION_ID)

    assert fact.observed_tax_code_refs == (
        f"erp:tax.tax_code:{VAT_CODE_ID}",
        f"erp:tax.tax_code:{WHT_2_CODE_ID}",
    )
    # And they are NOT smuggled into the module payload.
    assert "observed_tax_code_refs" not in to_tax_fact_kwargs(fact)


def test_an_unloaded_line_taxes_relationship_yields_no_evidence():
    """The mapper must not lazy-load inside an adapter."""
    invoice = build_ar_invoice()
    fact = ar_invoice_line_fact(
        invoice, build_ar_line(invoice), jurisdiction_id=JURISDICTION_ID
    )
    assert fact.observed_tax_code_refs == ()


# ============================ THE TWO-VAT-ROWS ANTI-PATTERN, ASSERTED IN CODE


def test_a_source_fact_cannot_express_inclusiveness_or_a_tax_code():
    """ERP's `VAT-7.5` and `VAT-7.5 (inclusive)` differ ONLY in `is_inclusive`.

    That is the "variant as a new row" pattern this programme removes.  In
    `dotmac-tax`, `inclusive` is a column on a tax RULE selected by
    jurisdiction/party/supply/place — so the two ERP rows map to ONE module tax
    code carrying two effective rules.

    The proof is structural rather than documentary: a source fact has nowhere
    to put either an inclusive flag or a chosen tax code, so an ERP caller
    CANNOT ask for the inclusive variant.  Determination selects the rule.
    """
    fields = set(ERPSourceTaxFactV1.__dataclass_fields__)
    assert not fields & {"inclusive", "is_inclusive", "tax_code_id", "tax_rate", "rate"}

    invoice = build_ar_invoice()
    fact = ar_invoice_line_fact(
        invoice, build_ar_line(invoice), jurisdiction_id=JURISDICTION_ID
    )
    module_kwargs = to_tax_fact_kwargs(fact)
    assert not set(module_kwargs) & {"inclusive", "tax_code_id", "rate"}


# ================================================================== INBOUND: AP


def test_ap_supplier_invoice_line_fact_maps_every_field():
    invoice = build_ap_invoice()
    line = build_ap_line(invoice)
    line.line_taxes = [
        SupplierInvoiceLineTax(
            line_tax_id=uuid.uuid4(),
            line_id=line.line_id,
            tax_code_id=VAT_CODE_ID,
            base_amount=Decimal("100000.00"),
            tax_rate=Decimal("0.075000"),
            tax_amount=Decimal("7500.00"),
            sequence=1,
        )
    ]

    fact = ap_supplier_invoice_line_fact(invoice, line, jurisdiction_id=JURISDICTION_ID)

    assert fact.family is SourceFactFamily.AP_INVOICE_LINE
    assert fact.fact_kind == "ap_invoice_line"
    assert fact.transaction_side is TransactionSide.INPUT
    assert fact.source_ref == f"erp:ap.supplier_invoice_line:{line.line_id}"
    assert fact.evidence_ref == f"erp:ap.supplier_invoice:{invoice.invoice_id}"
    assert fact.counterparty_ref == f"erp:supplier:{invoice.supplier_id}"
    assert fact.source_version.startswith("cv2:")
    assert fact.base_amount == ngn("100000.00")
    assert fact.observed_tax_code_refs == (f"erp:tax.tax_code:{VAT_CODE_ID}",)


def test_ap_credit_note_is_a_reversal():
    invoice = build_ap_invoice(invoice_type=SupplierInvoiceType.CREDIT_NOTE)
    line = build_ap_line(invoice, line_amount=Decimal("-1000.00"))
    fact = ap_supplier_invoice_line_fact(invoice, line, jurisdiction_id=JURISDICTION_ID)
    assert fact.reversal is True
    assert fact.base_amount == ngn("1000.00")


def test_ap_mapper_never_decides_recoverability():
    """Recoverability is a module rule property, not an ERP input."""
    fields = set(ERPSourceTaxFactV1.__dataclass_fields__)
    assert not fields & {"is_recoverable", "recoverable_amount", "recovery_rate"}


# ============================================================= INBOUND: payroll


def test_payroll_taxable_pay_fact_carries_the_annualised_base():
    slip_id = uuid.uuid4()
    entry_id = uuid.uuid4()
    employee_id = uuid.uuid4()

    fact = payroll_taxable_pay_fact(
        organization_id=ORG_ID,
        jurisdiction_id=JURISDICTION_ID,
        employee_id=employee_id,
        slip_id=slip_id,
        payroll_entry_id=entry_id,
        period_end=date(2026, 3, 31),
        annual_taxable_income=Decimal("7200000.00"),
        currency_code=NGN,
    )

    assert fact.family is SourceFactFamily.PAYROLL_TAXABLE_PAY
    assert fact.transaction_side is TransactionSide.LIABILITY
    assert fact.recognition_basis_code == "payroll_period"
    assert fact.occurred_on == date(2026, 3, 31)
    assert fact.base_amount == ngn("7200000.00")
    assert fact.source_ref == f"erp:payroll.salary_slip:{slip_id}:paye"
    assert fact.evidence_ref == f"erp:payroll.payroll_entry:{entry_id}"
    assert fact.counterparty_ref == f"erp:employee:{employee_id}"
    assert fact.reversal is False


def test_negative_payroll_taxable_pay_is_refused():
    with pytest.raises(TaxAdapterRefusal, match="negative taxable base"):
        payroll_taxable_pay_fact(
            organization_id=ORG_ID,
            jurisdiction_id=JURISDICTION_ID,
            employee_id=uuid.uuid4(),
            slip_id=uuid.uuid4(),
            period_end=date(2026, 3, 31),
            annual_taxable_income=Decimal("-100.00"),
            currency_code=NGN,
        )


# ================================================ INBOUND: the money boundary


def test_a_float_amount_is_refused_at_the_seam():
    invoice = build_ar_invoice()
    line = build_ar_line(invoice)
    line.line_amount = 100000.00  # deliberately a float, not a Decimal
    with pytest.raises(TaxAdapterRefusal, match="Decimal"):
        ar_invoice_line_fact(invoice, line, jurisdiction_id=JURISDICTION_ID)


def test_an_unprovisioned_currency_is_refused_rather_than_guessed():
    invoice = build_ar_invoice(currency_code="XAF")
    with pytest.raises(TaxAdapterRefusal):
        ar_invoice_line_fact(
            invoice, build_ar_line(invoice), jurisdiction_id=JURISDICTION_ID
        )


def test_sub_minor_unit_precision_is_refused_rather_than_rounded():
    invoice = build_ar_invoice()
    line = build_ar_line(invoice, line_amount=Decimal("100000.005"))
    with pytest.raises(TaxAdapterRefusal, match="minor-unit precision"):
        ar_invoice_line_fact(invoice, line, jurisdiction_id=JURISDICTION_ID)


# ====================================== INBOUND: source_version is CONTENT

# `VersionedMixin.version` is an optimistic-locking counter, not a content
# revision, and it fails in both directions: it UNDER-counts (a line edit that
# does not bump the header reuses a version whose facts changed — loud, the
# module raises TaxConflict) and it OVER-counts (any unrelated update bumps it,
# so unchanged facts get a new version, match no existing row under
# `uq_tax_determination_sets_source`, and create a SECOND determination set with
# an identical fingerprint — silent duplicate statutory evidence). These tests
# pin the digest that closes both.


def test_the_header_version_column_does_not_reach_the_source_version():
    """OVER-COUNT: an unrelated header bump must not re-version the fact."""
    invoice_id = uuid.uuid4()
    line_id = uuid.uuid4()
    before = build_ar_invoice(invoice_id=invoice_id, version=3)
    after = build_ar_invoice(invoice_id=invoice_id, version=97)

    first = ar_invoice_line_fact(
        before, build_ar_line(before, line_id=line_id), jurisdiction_id=JURISDICTION_ID
    )
    second = ar_invoice_line_fact(
        after, build_ar_line(after, line_id=line_id), jurisdiction_id=JURISDICTION_ID
    )

    assert first.source_ref == second.source_ref
    assert first.source_version == second.source_version


def test_resubmitting_an_unchanged_row_yields_an_identical_digest():
    """Idempotent no-op at the module, instead of a duplicate determination set."""
    invoice = build_ar_invoice()
    line_id = uuid.uuid4()
    versions = {
        ar_invoice_line_fact(
            invoice,
            build_ar_line(invoice, line_id=line_id),
            jurisdiction_id=JURISDICTION_ID,
        ).source_version
        for _ in range(4)
    }
    assert len(versions) == 1


def test_a_changed_tax_fact_yields_a_different_source_version():
    """UNDER-COUNT: a line edit re-versions even though no header moved."""
    invoice = build_ar_invoice(version=3)
    line_id = uuid.uuid4()
    baseline = ar_invoice_line_fact(
        invoice,
        build_ar_line(invoice, line_id=line_id),
        jurisdiction_id=JURISDICTION_ID,
    ).source_version

    changed_amount = ar_invoice_line_fact(
        invoice,
        build_ar_line(invoice, line_id=line_id, line_amount=Decimal("100000.01")),
        jurisdiction_id=JURISDICTION_ID,
    ).source_version
    changed_item = ar_invoice_line_fact(
        invoice,
        build_ar_line(invoice, line_id=line_id, item_id=uuid.uuid4()),
        jurisdiction_id=JURISDICTION_ID,
    ).source_version
    changed_date = ar_invoice_line_fact(
        build_ar_invoice(invoice_id=invoice.invoice_id, invoice_date=date(2026, 4, 1)),
        build_ar_line(invoice, line_id=line_id),
        jurisdiction_id=JURISDICTION_ID,
    ).source_version
    changed_basis = ar_invoice_line_fact(
        invoice,
        build_ar_line(invoice, line_id=line_id),
        jurisdiction_id=JURISDICTION_ID,
        recognition_basis_code="cash",
    ).source_version
    changed_place = ar_invoice_line_fact(
        invoice,
        build_ar_line(invoice, line_id=line_id),
        jurisdiction_id=JURISDICTION_ID,
        place_ref="erp:place:lagos",
    ).source_version
    credit_note = build_ar_invoice(
        invoice_type=InvoiceType.CREDIT_NOTE, invoice_id=invoice.invoice_id
    )
    changed_reversal = ar_invoice_line_fact(
        credit_note,
        build_ar_line(credit_note, line_id=line_id),
        jurisdiction_id=JURISDICTION_ID,
    ).source_version
    changed_correlation = ar_invoice_line_fact(
        invoice,
        build_ar_line(invoice, line_id=line_id),
        jurisdiction_id=JURISDICTION_ID,
        correlation_ref="erp:correlation:replacement",
    ).source_version

    distinct = {
        baseline,
        changed_amount,
        changed_item,
        changed_date,
        changed_basis,
        changed_place,
        changed_reversal,
        changed_correlation,
    }
    assert len(distinct) == 8


def test_observed_tax_code_order_is_not_a_content_change():
    """The legacy `line_taxes` collection has no meaningful order here."""
    invoice = build_ar_invoice()
    line_id = uuid.uuid4()

    def fact_with(sequences):
        line = build_ar_line(invoice, line_id=line_id)
        line.line_taxes = [
            InvoiceLineTax(
                line_tax_id=uuid.uuid4(),
                line_id=line_id,
                tax_code_id=code_id,
                base_amount=Decimal("100000.00"),
                tax_rate=Decimal("0.075000"),
                tax_amount=Decimal("7500.00"),
                sequence=sequence,
            )
            for code_id, sequence in sequences
        ]
        return ar_invoice_line_fact(invoice, line, jurisdiction_id=JURISDICTION_ID)

    forward = fact_with([(VAT_CODE_ID, 1), (WHT_2_CODE_ID, 2)])
    backward = fact_with([(WHT_2_CODE_ID, 2), (VAT_CODE_ID, 1)])
    assert forward.source_version == backward.source_version

    # A different SET of observed codes is a content change.
    fewer = fact_with([(VAT_CODE_ID, 1)])
    assert fewer.source_version != forward.source_version


def test_float_money_can_never_reach_the_digest():
    """Digesting a float would make the version depend on binary rounding."""
    from app.services.finance.tax.adoption.inbound import _exact_money_text

    class FloatMoney:
        amount = 100000.0
        currency = ngn("1.00").currency

    with pytest.raises(TaxAdapterRefusal, match="must be an exact Decimal"):
        _exact_money_text(FloatMoney())


def test_equal_money_written_at_different_scales_digests_identically():
    """`Decimal("100.5")` and `Decimal("100.50")` are the same money."""
    from app.services.finance.tax.adoption.inbound import _exact_money_text

    currency = ngn("1.00").currency
    loose = type(ngn("1.00"))(amount=Decimal("100.5"), currency=currency)
    tight = type(ngn("1.00"))(amount=Decimal("100.50"), currency=currency)
    assert _exact_money_text(loose) == _exact_money_text(tight) == "100.50"


def test_every_family_derives_its_version_the_same_way():
    ap_invoice = build_ap_invoice()
    payroll = payroll_taxable_pay_fact(
        organization_id=ORG_ID,
        jurisdiction_id=JURISDICTION_ID,
        employee_id=uuid.uuid4(),
        slip_id=uuid.uuid4(),
        period_end=date(2026, 3, 31),
        annual_taxable_income=Decimal("7200000.00"),
        currency_code=NGN,
    )
    ap = ap_supplier_invoice_line_fact(
        ap_invoice, build_ap_line(ap_invoice), jurisdiction_id=JURISDICTION_ID
    )
    for fact in (ap, payroll):
        assert fact.source_version.startswith("cv2:")
        assert len(fact.source_version) == len("cv2:") + 64


# ============================================ INBOUND: the module translation


def test_to_tax_fact_kwargs_matches_the_contract_field_list_exactly():
    invoice = build_ar_invoice()
    fact = ar_invoice_line_fact(
        invoice, build_ar_line(invoice), jurisdiction_id=JURISDICTION_ID
    )
    assert set(to_tax_fact_kwargs(fact)) == set(TaxFact.__dataclass_fields__)


def test_every_erp_only_field_is_dropped_before_the_module_sees_it():
    invoice = build_ar_invoice()
    fact = ar_invoice_line_fact(
        invoice, build_ar_line(invoice), jurisdiction_id=JURISDICTION_ID
    )
    kwargs = to_tax_fact_kwargs(fact)
    for erp_only in (
        "organization_id",
        "tenant_id",
        "family",
        "document_id",
        "line_id",
        "reversal",
        "correlation_ref",
        "observed_tax_code_refs",
    ):
        assert erp_only not in kwargs
    # The side crosses as the module's plain string vocabulary, not an ERP enum.
    assert kwargs["transaction_side"] == "output"
    assert not isinstance(kwargs["transaction_side"], TransactionSide)


def test_an_unmapped_cohort_is_refused_by_name():
    assert UNMAPPED_FAMILIES
    assert MAPPED_FAMILIES.isdisjoint(UNMAPPED_FAMILIES)
    family = sorted(UNMAPPED_FAMILIES, key=lambda item: item.value)[0]
    fact = ERPSourceTaxFactV1(
        organization_id=ORG_ID,
        jurisdiction_id=JURISDICTION_ID,
        family=family,
        fact_kind=family.value,
        recognition_basis_code="accrual",
        transaction_side=TransactionSide.WITHHOLDING,
        occurred_on=date(2026, 3, 31),
        base_amount=ngn("1000.00"),
        source_ref="erp:hand-built",
        source_version="v1",
        evidence_ref="erp:hand-built",
        document_id=uuid.uuid4(),
    )
    with pytest.raises(TaxAdapterRefusal, match="no C1 mapper"):
        to_tax_fact_kwargs(fact)


# ===================================================== OUTBOUND: determinations


def vat_component(*, sequence: int = 100, inclusive: bool = False, recoverable=True):
    tax = ngn("7500.00")
    zero = ngn("0.00")
    return TaxDeterminationComponentV1(
        determination_id=uuid.uuid4(),
        determination_set_id=DETERMINATION_SET_ID,
        component_sequence=sequence,
        tax_code_id=VAT_CODE_ID,
        rule_id=uuid.uuid4(),
        rule_version=1,
        treatment_code="standard_rated",
        calculation_base_code="source_amount",
        inclusive=inclusive,
        party_category=None,
        supply_category=None,
        place_code=None,
        party_classification_id=None,
        supply_classification_id=None,
        place_classification_id=None,
        base_amount=ngn("100000.00"),
        tax_amount=tax,
        recoverable_amount=tax if recoverable else zero,
        non_recoverable_amount=zero if recoverable else tax,
        lines=(
            TaxDeterminationLineV1(
                sequence=1,
                taxable_amount=ngn("100000.00"),
                rate=Decimal("0.075"),
                tax_amount=tax,
            ),
        ),
    )


def paye_component(*, sequence: int = 100):
    """A progressive PAYE result; ERP consumes band lines without recalculating."""
    tax = ngn("7500.00")
    zero = ngn("0.00")
    return TaxDeterminationComponentV1(
        determination_id=uuid.uuid4(),
        determination_set_id=DETERMINATION_SET_ID,
        component_sequence=sequence,
        tax_code_id=PAYE_CODE_ID,
        rule_id=uuid.uuid4(),
        rule_version=1,
        treatment_code="standard_rated",
        calculation_base_code="source_amount",
        inclusive=False,
        party_category=None,
        supply_category=None,
        place_code=None,
        party_classification_id=None,
        supply_classification_id=None,
        place_classification_id=None,
        base_amount=ngn("100000.00"),
        tax_amount=tax,
        recoverable_amount=zero,
        non_recoverable_amount=tax,
        lines=(
            TaxDeterminationLineV1(
                sequence=1,
                taxable_amount=ngn("25000.00"),
                rate=Decimal("0"),
                tax_amount=zero,
            ),
            TaxDeterminationLineV1(
                sequence=2,
                taxable_amount=ngn("75000.00"),
                rate=Decimal("0.10"),
                tax_amount=tax,
            ),
        ),
    )


def wht_component(*, sequence: int = 200):
    """WHT 2 %: compound (on source + prior tax) and NOT recoverable."""
    tax = ngn("2150.00")
    return TaxDeterminationComponentV1(
        determination_id=uuid.uuid4(),
        determination_set_id=DETERMINATION_SET_ID,
        component_sequence=sequence,
        tax_code_id=WHT_2_CODE_ID,
        rule_id=uuid.uuid4(),
        rule_version=1,
        treatment_code="standard_rated",
        calculation_base_code="source_plus_prior_tax",
        inclusive=False,
        party_category=None,
        supply_category=None,
        place_code=None,
        party_classification_id=None,
        supply_classification_id=None,
        place_classification_id=None,
        base_amount=ngn("107500.00"),
        tax_amount=tax,
        recoverable_amount=ngn("0.00"),
        non_recoverable_amount=tax,
        lines=(
            TaxDeterminationLineV1(
                sequence=1,
                taxable_amount=ngn("107500.00"),
                rate=Decimal("0.02"),
                tax_amount=tax,
            ),
        ),
    )


def exempt_component(*, sequence: int = 300):
    zero = ngn("0.00")
    return TaxDeterminationComponentV1(
        determination_id=uuid.uuid4(),
        determination_set_id=DETERMINATION_SET_ID,
        component_sequence=sequence,
        tax_code_id=STAMP_DUTY_CODE_ID,
        rule_id=uuid.uuid4(),
        rule_version=1,
        treatment_code="exempt",
        calculation_base_code="source_amount",
        inclusive=False,
        party_category=None,
        supply_category=None,
        place_code=None,
        party_classification_id=None,
        supply_classification_id=None,
        place_classification_id=None,
        base_amount=ngn("100000.00"),
        tax_amount=zero,
        recoverable_amount=zero,
        non_recoverable_amount=zero,
        lines=(
            TaxDeterminationLineV1(
                sequence=1,
                taxable_amount=ngn("100000.00"),
                rate=None,
                tax_amount=zero,
            ),
        ),
    )


def zero_treatment_component(
    *, sequence: int, treatment: str
) -> TaxDeterminationComponentV1:
    """A component carrying a configured zero treatment.

    `zero_rated`, `exempt` and `out_of_scope` are DISTINCT legal answers that
    all produce no money, and the module keeps them distinct on purpose.
    """
    zero = ngn("0.00")
    return TaxDeterminationComponentV1(
        determination_id=uuid.uuid4(),
        determination_set_id=DETERMINATION_SET_ID,
        component_sequence=sequence,
        tax_code_id=STAMP_DUTY_CODE_ID,
        rule_id=uuid.uuid4(),
        rule_version=1,
        treatment_code=treatment,
        calculation_base_code="source_amount",
        inclusive=False,
        party_category=None,
        supply_category=None,
        place_code=None,
        party_classification_id=None,
        supply_classification_id=None,
        place_classification_id=None,
        base_amount=ngn("100000.00"),
        tax_amount=zero,
        recoverable_amount=zero,
        non_recoverable_amount=zero,
        lines=(
            TaxDeterminationLineV1(
                sequence=1,
                taxable_amount=ngn("100000.00"),
                rate=None,
                tax_amount=zero,
            ),
        ),
    )


def build_result(
    *,
    components=None,
    source: str = "100000.00",
    net: str = "100000.00",
    tax: str = "7500.00",
    gross: str = "107500.00",
    fingerprint: str = FINGERPRINT,
    transaction_side: str = "output",
    reversal: bool = False,
) -> TaxDeterminationSetV1:
    family = {
        "output": SourceFactFamily.AR_INVOICE_LINE,
        "input": SourceFactFamily.AP_INVOICE_LINE,
        "withholding": SourceFactFamily.AR_SETTLEMENT_WITHHOLDING,
        "liability": SourceFactFamily.PAYROLL_TAXABLE_PAY,
    }[transaction_side]
    source_ref = {
        SourceFactFamily.AR_INVOICE_LINE: f"erp:ar.invoice_line:{LINE_ID}",
        SourceFactFamily.AP_INVOICE_LINE: (f"erp:ap.supplier_invoice_line:{LINE_ID}"),
        SourceFactFamily.AR_SETTLEMENT_WITHHOLDING: "erp:ar.settlement:wht",
        SourceFactFamily.PAYROLL_TAXABLE_PAY: (
            f"erp:payroll.salary_slip:{DOCUMENT_ID}:paye"
        ),
    }[family]
    evidence_ref = {
        SourceFactFamily.AR_INVOICE_LINE: f"erp:ar.invoice:{DOCUMENT_ID}",
        SourceFactFamily.AP_INVOICE_LINE: (f"erp:ap.supplier_invoice:{DOCUMENT_ID}"),
        SourceFactFamily.AR_SETTLEMENT_WITHHOLDING: "erp:ar.settlement:wht",
        SourceFactFamily.PAYROLL_TAXABLE_PAY: f"erp:payroll.salary_slip:{DOCUMENT_ID}",
    }[family]
    counterparty_ref = {
        SourceFactFamily.AR_INVOICE_LINE: "erp:customer:123",
        SourceFactFamily.AP_INVOICE_LINE: "erp:supplier:123",
        SourceFactFamily.AR_SETTLEMENT_WITHHOLDING: "erp:customer:123",
        SourceFactFamily.PAYROLL_TAXABLE_PAY: "erp:employee:123",
    }[family]
    result = TaxDeterminationSetV1(
        tenant_id=ORG_ID,
        determination_set_id=DETERMINATION_SET_ID,
        jurisdiction_id=JURISDICTION_ID,
        occurred_on=date(2026, 3, 31),
        fact_kind=family.value,
        recognition_basis_code=(
            "payroll_period"
            if family is SourceFactFamily.PAYROLL_TAXABLE_PAY
            else "accrual"
        ),
        transaction_side=transaction_side,
        source_amount=ngn(source),
        net_amount=ngn(net),
        tax_amount=ngn(tax),
        gross_amount=ngn(gross),
        source_ref=source_ref,
        source_version="pending",
        source_fingerprint=fingerprint,
        result_fingerprint=RESULT_FINGERPRINT,
        evidence_ref=evidence_ref,
        counterparty_ref=counterparty_ref,
        supply_ref=(
            None if family is SourceFactFamily.PAYROLL_TAXABLE_PAY else "erp:item:456"
        ),
        place_ref=None,
        determined_at=datetime(2026, 3, 31, 12, 0, tzinfo=UTC),
        components=tuple(components if components is not None else [vat_component()]),
    )
    submitted = source_fact_for_result(result, reversal=reversal)
    return replace(result, source_version=source_fact_content_version(submitted))


def application_context(
    *,
    consequence: AccountingConsequence = AccountingConsequence.AR_OUTPUT_TAX,
    functional_currency_code: str = NGN,
) -> TaxApplicationContextV1:
    return TaxApplicationContextV1(
        posting_date=date(2026, 3, 31),
        consequence=consequence,
        counterpart_account_id=COUNTERPART_ACCOUNT,
        functional_currency_code=functional_currency_code,
    )


def source_fact_for_result(
    result: TaxDeterminationSetV1,
    *,
    family: SourceFactFamily | None = None,
    reversal: bool = False,
    organization_id: uuid.UUID | None = None,
    document_id: uuid.UUID = DOCUMENT_ID,
    line_id: uuid.UUID | None = LINE_ID,
) -> ERPSourceTaxFactV1:
    family = family or SourceFactFamily(result.fact_kind)
    if family is SourceFactFamily.PAYROLL_TAXABLE_PAY:
        line_id = None
    return ERPSourceTaxFactV1(
        organization_id=organization_id or result.tenant_id,
        jurisdiction_id=result.jurisdiction_id,
        family=family,
        fact_kind=result.fact_kind,
        recognition_basis_code=result.recognition_basis_code,
        transaction_side=TransactionSide(result.transaction_side),
        occurred_on=result.occurred_on,
        base_amount=result.source_amount,
        source_ref=result.source_ref,
        source_version=result.source_version,
        evidence_ref=result.evidence_ref,
        document_id=document_id,
        line_id=line_id,
        counterparty_ref=result.counterparty_ref,
        supply_ref=result.supply_ref,
        place_ref=result.place_ref,
        reversal=reversal,
    )


def account_map(
    *, vat_collected=True, vat_paid=True, wht_expense=True
) -> TaxAccountMap:
    return TaxAccountMap(
        entries=(
            TaxCodeAccounts(
                tax_code_id=VAT_CODE_ID,
                collected_account_id=VAT_COLLECTED_ACCOUNT if vat_collected else None,
                paid_account_id=VAT_PAID_ACCOUNT if vat_paid else None,
            ),
            TaxCodeAccounts(
                tax_code_id=WHT_2_CODE_ID,
                collected_account_id=WHT_COLLECTED_ACCOUNT,
                expense_account_id=WHT_EXPENSE_ACCOUNT if wht_expense else None,
            ),
            TaxCodeAccounts(
                tax_code_id=STAMP_DUTY_CODE_ID,
                collected_account_id=uuid.uuid4(),
            ),
            TaxCodeAccounts(
                tax_code_id=PAYE_CODE_ID,
                collected_account_id=PAYE_PAYABLE_ACCOUNT,
            ),
        )
    )


def project(result: TaxDeterminationSetV1, **kwargs) -> ConsequencePosting:
    kwargs.setdefault("source_fact", source_fact_for_result(result))
    kwargs.setdefault("application", application_context())
    kwargs.setdefault("accounts", account_map())
    kwargs.setdefault("expected_fingerprint", result.source_fingerprint)
    kwargs.setdefault("fiscal_period_id", FISCAL_PERIOD_ID)
    return project_determination_set(result, **kwargs)


def test_ar_output_tax_credits_the_collected_account_and_balances():
    posting = project(build_result())

    assert len(posting.lines) == 2
    tax_line, counterpart = posting.lines
    assert tax_line.account_id == VAT_COLLECTED_ACCOUNT
    assert tax_line.credit_amount == Decimal("7500.00")
    assert tax_line.debit_amount == Decimal("0")
    assert tax_line.tax_code_id == VAT_CODE_ID
    assert counterpart.account_id == COUNTERPART_ACCOUNT
    assert counterpart.debit_amount == Decimal("7500.00")
    assert posting.total_debit == posting.total_credit
    assert posting.fiscal_period_id == FISCAL_PERIOD_ID
    assert posting.currency_code == NGN
    assert posting.result_fingerprint == RESULT_FINGERPRINT


def test_ap_input_tax_splits_recoverable_from_irrecoverable():
    """The one place a component yields two lines, and why the split is amounts."""
    result = build_result(
        components=[vat_component(), wht_component()],
        tax="9650.00",
        gross="109650.00",
        transaction_side="input",
    )

    posting = project(
        result,
        application=application_context(consequence=AccountingConsequence.AP_INPUT_TAX),
    )

    by_account = {line.account_id: line for line in posting.lines}
    assert by_account[VAT_PAID_ACCOUNT].debit_amount == Decimal("7500.00")
    assert by_account[WHT_EXPENSE_ACCOUNT].debit_amount == Decimal("2150.00")
    assert by_account[COUNTERPART_ACCOUNT].credit_amount == Decimal("9650.00")
    assert posting.total_debit == posting.total_credit == Decimal("9650.00")


def test_payroll_liability_projects_a_balanced_paye_payable():
    result = build_result(transaction_side="liability", components=[paye_component()])

    posting = project(
        result,
        application=application_context(
            consequence=AccountingConsequence.PAYROLL_TAX_PAYABLE
        ),
    )

    tax_line, counterpart = posting.lines
    assert tax_line.account_id == PAYE_PAYABLE_ACCOUNT
    assert tax_line.credit_amount == Decimal("7500.00")
    assert counterpart.account_id == COUNTERPART_ACCOUNT
    assert counterpart.debit_amount == Decimal("7500.00")
    assert posting.document_type == "PAYROLL_SALARY_SLIP"
    assert posting.total_debit == posting.total_credit


def test_reversal_flips_every_line_and_still_balances():
    result = build_result(reversal=True)
    posting = project(result, source_fact=source_fact_for_result(result, reversal=True))

    tax_line, counterpart = posting.lines
    assert tax_line.debit_amount == Decimal("7500.00")
    assert tax_line.credit_amount == Decimal("0")
    assert counterpart.credit_amount == Decimal("7500.00")
    assert posting.total_debit == posting.total_credit


def test_a_normal_determination_cannot_be_rebound_as_a_reversal():
    result = build_result()
    rebound = source_fact_for_result(result, reversal=True)
    with pytest.raises(TaxAdapterRefusal, match="source version does not match"):
        project(result, source_fact=rebound)


def test_a_submitted_correlation_cannot_be_rebound_at_projection():
    result = build_result()
    rebound = replace(
        source_fact_for_result(result), correlation_ref="erp:correlation:replacement"
    )
    with pytest.raises(TaxAdapterRefusal, match="source version does not match"):
        project(result, source_fact=rebound)


def test_changed_source_classification_cannot_reuse_a_determination():
    result = build_result()
    changed = replace(source_fact_for_result(result), party_category="exempt_customer")
    rebound = replace(changed, source_version=source_fact_content_version(changed))
    with pytest.raises(TaxAdapterRefusal, match="source version does not match"):
        project(result, source_fact=rebound)


def test_a_missing_account_mapping_refuses_the_whole_set():
    with pytest.raises(TaxAdapterRefusal, match="no collected account mapped"):
        project(build_result(), accounts=account_map(vat_collected=False))


def test_an_unmapped_tax_code_refuses_the_whole_set():
    empty = TaxAccountMap(entries=())
    with pytest.raises(TaxAdapterRefusal, match="no ERP account mapping"):
        project(build_result(), accounts=empty)


def test_two_mappings_for_one_tax_code_is_an_adjudication_not_last_one_wins():
    with pytest.raises(TaxAdapterRefusal, match="more than one account mapping"):
        TaxAccountMap(
            entries=(
                TaxCodeAccounts(
                    tax_code_id=VAT_CODE_ID, collected_account_id=VAT_COLLECTED_ACCOUNT
                ),
                TaxCodeAccounts(
                    tax_code_id=VAT_CODE_ID, collected_account_id=VAT_PAID_ACCOUNT
                ),
            )
        )


def test_a_changed_fingerprint_refuses_the_row():
    result = build_result()
    with pytest.raises(TaxAdapterRefusal, match="fingerprint changed"):
        project(result, expected_fingerprint="0" * 64)


def test_a_cross_organization_result_refuses_the_posting_context():
    other_org = uuid.UUID("dddddddd-0000-4000-8000-000000000001")
    with pytest.raises(TaxAdapterRefusal, match="tenant does not match"):
        result = build_result()
        project(
            result,
            source_fact=source_fact_for_result(result, organization_id=other_org),
        )


@pytest.mark.parametrize(
    ("transaction_side", "wrong_consequence"),
    (
        ("output", AccountingConsequence.AP_INPUT_TAX),
        ("input", AccountingConsequence.AR_OUTPUT_TAX),
        ("liability", AccountingConsequence.AR_OUTPUT_TAX),
    ),
)
def test_accounting_consequence_cannot_contradict_the_determination_side(
    transaction_side: str,
    wrong_consequence: AccountingConsequence,
):
    with pytest.raises(TaxAdapterRefusal, match="consequence contradicts"):
        project(
            build_result(transaction_side=transaction_side),
            application=application_context(consequence=wrong_consequence),
        )


def test_settlement_withholding_stays_sealed_until_both_directions_exist():
    result = build_result(transaction_side="withholding")
    with pytest.raises(TaxAdapterRefusal, match="settlement withholding"):
        project(
            result,
            source_fact=source_fact_for_result(result),
            application=application_context(
                consequence=AccountingConsequence.WITHHOLDING_PAYABLE
            ),
        )


def test_application_context_accepts_only_a_currency_identity_not_an_fx_scalar():
    context = application_context(functional_currency_code="ngn")
    assert context.functional_currency_code == NGN
    with pytest.raises(TaxAdapterRefusal, match="three-letter"):
        replace(context, functional_currency_code="NG")


def test_foreign_currency_is_structurally_refused_until_the_c3_fx_contract():
    with pytest.raises(TaxAdapterRefusal, match="foreign-currency"):
        project(
            build_result(),
            application=application_context(functional_currency_code="USD"),
        )


def test_result_cannot_be_rebound_to_another_document():
    result = build_result()
    other_document = uuid.UUID("ffffffff-0000-4000-8000-000000000001")
    with pytest.raises(TaxAdapterRefusal, match="evidence does not bind"):
        project(
            result,
            source_fact=source_fact_for_result(result, document_id=other_document),
        )


def test_result_must_match_the_exact_submitted_source_fact():
    result = build_result()
    source_fact = replace(source_fact_for_result(result), source_version="v4")
    with pytest.raises(TaxAdapterRefusal, match="source version does not match"):
        project(result, source_fact=source_fact)


def test_only_the_public_result_contract_crosses_the_boundary():
    with pytest.raises(TaxAdapterRefusal, match="TaxDeterminationSetV1"):
        project_determination_set(  # type: ignore[arg-type]
            object(),
            source_fact=source_fact_for_result(build_result()),
            application=application_context(),
            accounts=account_map(),
            expected_fingerprint=FINGERPRINT,
            fiscal_period_id=FISCAL_PERIOD_ID,
        )


def test_an_open_period_cannot_be_asserted_by_omission():
    with pytest.raises(TaxAdapterRefusal, match="open fiscal period id is required"):
        project_determination_set(
            (result := build_result()),
            source_fact=source_fact_for_result(result),
            application=application_context(),
            accounts=account_map(),
            expected_fingerprint=FINGERPRINT,
            fiscal_period_id=None,  # type: ignore[arg-type]
        )


# ================================== OUTBOUND: the inclusive/compound arithmetic


def test_an_inclusive_set_requires_source_to_equal_gross():
    """`VAT-7.5 (inclusive)` becomes rule-level treatment, and the arithmetic
    that distinguishes it is re-derived here rather than trusted."""
    result = build_result(
        components=[vat_component(inclusive=True)],
        source="107500.00",
        net="100000.00",
        tax="7500.00",
        gross="107500.00",
    )
    posting = project(result)
    assert posting.total_debit == posting.total_credit == Decimal("7500.00")


def test_an_inclusive_set_whose_source_equals_net_is_refused():
    with pytest.raises(ValueError, match="source amount must equal gross"):
        build_result(components=[vat_component(inclusive=True)])


def test_an_exclusive_set_whose_source_equals_gross_is_refused():
    with pytest.raises(ValueError, match="source amount must equal net"):
        build_result(source="107500.00")


def test_an_inclusive_component_beside_any_other_is_refused():
    """The public a3 contract refuses the ambiguous source amount."""
    with pytest.raises(ValueError, match="cannot be combined"):
        build_result(
            components=[vat_component(inclusive=True), wht_component()],
            source="107500.00",
            net="100000.00",
            tax="9650.00",
            gross="109650.00",
        )


def test_components_out_of_calculation_order_are_refused():
    with pytest.raises(ValueError, match="strict unique ordering"):
        build_result(
            components=[wht_component(sequence=100), vat_component(sequence=50)],
            tax="9650.00",
            gross="109650.00",
        )


def test_a_component_whose_recovery_split_does_not_add_up_is_refused():
    with pytest.raises(ValueError, match="recovery split"):
        replace(vat_component(), non_recoverable_amount=ngn("0.01"))


def test_components_that_do_not_total_the_set_are_refused():
    with pytest.raises(ValueError, match="components must total"):
        build_result(tax="9999.00", gross="109999.00")


def test_net_plus_tax_must_equal_gross():
    with pytest.raises(ValueError, match="net plus tax must equal gross"):
        build_result(gross="107500.01")


# ================================================ OUTBOUND: zero treatments


def test_a_zero_treatment_component_is_carried_not_dropped():
    result = build_result(components=[vat_component(), exempt_component()])

    posting = project(result)

    assert posting.is_postable is True
    assert len(posting.lines) == 2, "the exempt component produces no journal line"
    assert len(posting.reportable_zero_components) == 1
    assert posting.reportable_zero_components[0].treatment_code == "exempt"


def _all_exempt() -> TaxDeterminationSetV1:
    return build_result(components=[exempt_component()], tax="0.00", gross="100000.00")


def test_an_all_zero_set_returns_its_reportable_components_and_posts_nothing():
    """C1.1: an exempt supply is an ANSWER, not a refusal.

    This previously raised `TaxAdapterRefusal`, which put the most ordinary
    outcome in tax on the same path as a changed fingerprint or an ambiguous
    account, and left the reportable components unreachable — so a caller could
    not do the one thing the message told it to do.
    """
    posting = project(_all_exempt())

    assert posting.is_postable is False
    assert posting.lines == ()
    assert len(posting.reportable_zero_components) == 1
    assert posting.reportable_zero_components[0].treatment_code == "exempt"


def test_an_all_zero_set_renders_no_journal_at_all():
    """A zero-line journal must never reach JournalService.

    `_require_balanced` would happily accept it — zero equals zero — so the
    guard has to be here, where "nothing to post" is still distinguishable from
    "balanced".
    """
    assert project(_all_exempt()).to_journal_input() is None


def test_an_all_zero_set_emits_no_counterpart_line():
    """The counterpart exists to balance tax; with no tax there is nothing to balance."""
    posting = project(_all_exempt())

    assert all(line.account_id != COUNTERPART_ACCOUNT for line in posting.lines)
    assert posting.total_debit == posting.total_credit == Decimal("0")


def test_a_reversing_all_zero_set_is_still_reportable_and_still_posts_nothing():
    """Reversal flips sides; it cannot manufacture a line where there is no tax."""
    result = build_result(
        components=[exempt_component()],
        tax="0.00",
        gross="100000.00",
        reversal=True,
    )

    posting = project(result, source_fact=source_fact_for_result(result, reversal=True))

    assert posting.is_postable is False
    assert posting.lines == ()
    assert len(posting.reportable_zero_components) == 1


def test_every_zero_treatment_is_reportable_not_only_exempt():
    """All three zero treatments survive to the return box, distinctly."""
    result = build_result(
        components=[
            zero_treatment_component(sequence=sequence, treatment=treatment)
            for sequence, treatment in enumerate(
                ("zero_rated", "exempt", "out_of_scope"), start=1
            )
        ],
        tax="0.00",
        gross="100000.00",
    )

    posting = project(result)

    assert posting.is_postable is False
    assert {c.treatment_code for c in posting.reportable_zero_components} == {
        "zero_rated",
        "exempt",
        "out_of_scope",
    }


def test_a_postable_set_is_unchanged_by_the_reportable_only_path():
    """Regression: C1.1 must not alter the ordinary path."""
    posting = project(build_result())

    assert posting.is_postable is True
    journal = posting.to_journal_input()
    assert journal is not None
    assert len(journal.lines) == 2


def test_a_standard_rated_component_holding_no_tax_is_a_defect():
    component = replace(
        exempt_component(),
        treatment_code="standard_rated",
    )
    result = build_result(components=[component], tax="0.00", gross="100000.00")
    with pytest.raises(TaxAdapterRefusal, match="determination defect"):
        project(result)


def test_a_zero_treatment_holding_money_is_refused_at_the_contract():
    with pytest.raises(ValueError, match="must have zero tax"):
        replace(
            exempt_component(),
            tax_amount=ngn("1.00"),
            recoverable_amount=ngn("1.00"),
            lines=(
                TaxDeterminationLineV1(
                    sequence=1,
                    taxable_amount=ngn("100000.00"),
                    rate=None,
                    tax_amount=ngn("1.00"),
                ),
            ),
        )


# ============================================ OUTBOUND: the accounting owner


def test_the_projection_renders_into_the_accounting_owners_own_input_type():
    posting = project(build_result())

    journal = posting.to_journal_input()

    assert journal is not None
    assert journal.source_module == SOURCE_MODULE
    assert journal.source_document_type == "AR_INVOICE"
    assert journal.source_document_id == posting.document_id
    assert journal.reference == posting.source_ref
    assert journal.currency_code == NGN
    assert journal.posting_date == date(2026, 3, 31)
    debits = sum(line.debit_amount for line in journal.lines)
    credits = sum(line.credit_amount for line in journal.lines)
    assert debits == credits, "JournalService._require_balanced admits no tolerance"


def test_the_module_is_never_asked_about_an_account_or_a_journal():
    """The public result has no ERP account or journal fields."""
    import app.services.finance.tax.adoption.outbound as outbound

    source = (outbound.__file__ or "").replace(".pyc", ".py")
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "from dotmac_tax import TaxDeterminationComponentV1" in text
    assert "TaxDeterminationSetV1" in text
    assert "dotmac_tax.models" not in text
    assert "dotmac_tax.contracts" not in text

    component_fields = set(TaxDeterminationComponentV1.__dataclass_fields__)
    assert not component_fields & {
        "account_id",
        "journal_entry_id",
        "fiscal_period_id",
        "debit_amount",
        "credit_amount",
    }


def test_erp_has_deleted_the_temporary_determination_mirrors():
    import app.services.finance.tax.adoption.contracts as contracts

    assert not hasattr(contracts, "ApplyTaxDeterminationSetV1")
    assert not hasattr(contracts, "TaxDeterminationComponentV1")
    assert not hasattr(contracts, "MIRROR_RETIREMENT_GATE")
