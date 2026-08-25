"""C1: the two typed `dotmac-tax` seams, exercised through their real contracts.

Every test here builds the SAME value objects a production caller builds —
`ERPSourceTaxFactV1` via the named family mappers, `ApplyTaxDeterminationSetV1`
via its constructor — rather than poking at private helpers.  Nothing installs,
pins or composes `dotmac-tax`: the only test that touches the distribution is
`test_tax_fact_field_mirror_matches_the_installed_contract`, which skips when it
is absent and asserts ERP's mirror is exact when it is present.

The amounts are the ones ERP actually runs on in production: VAT 7.5 %, WHT 2 %
compounding on top of it, and a 1 % stamp duty.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.finance.ap.supplier_invoice import SupplierInvoice, SupplierInvoiceType
from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
from app.models.finance.ap.supplier_invoice_line_tax import SupplierInvoiceLineTax
from app.models.finance.ar.invoice import Invoice, InvoiceType
from app.models.finance.ar.invoice_line import InvoiceLine
from app.models.finance.ar.invoice_line_tax import InvoiceLineTax
from app.services.finance.money_boundary import to_boundary_money
from app.services.finance.tax.adoption import (
    MAPPED_FAMILIES,
    TAX_FACT_FIELDS,
    UNMAPPED_FAMILIES,
    AccountingConsequence,
    ApplyTaxDeterminationSetV1,
    ConsequencePosting,
    ERPSourceTaxFactV1,
    SourceFactFamily,
    TaxAccountMap,
    TaxAdapterRefusal,
    TaxDeterminationComponentV1,
    TransactionSide,
    ap_supplier_invoice_line_fact,
    ar_invoice_line_fact,
    payroll_taxable_pay_fact,
    project_determination_set,
    to_tax_fact_kwargs,
)
from app.services.finance.tax.adoption.outbound import SOURCE_MODULE, TaxCodeAccounts

NGN = "NGN"
JURISDICTION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
ORG_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")

VAT_CODE_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
WHT_2_CODE_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")
STAMP_DUTY_CODE_ID = uuid.UUID("55555555-5555-4555-8555-555555555555")

VAT_COLLECTED_ACCOUNT = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")
VAT_PAID_ACCOUNT = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000002")
WHT_EXPENSE_ACCOUNT = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000003")
WHT_COLLECTED_ACCOUNT = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000004")
COUNTERPART_ACCOUNT = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000009")
FISCAL_PERIOD_ID = uuid.UUID("bbbbbbbb-0000-4000-8000-000000000001")

FINGERPRINT = "f" * 64


def ngn(amount: str):
    return to_boundary_money(Decimal(amount), NGN)


# ---------------------------------------------------------------- AR / AP rows


def build_ar_invoice(
    *,
    invoice_type: InvoiceType = InvoiceType.STANDARD,
    invoice_id: uuid.UUID | None = None,
    version: int = 3,
    currency_code: str = NGN,
) -> Invoice:
    return Invoice(
        invoice_id=invoice_id or uuid.uuid4(),
        organization_id=ORG_ID,
        customer_id=uuid.UUID("66666666-6666-4666-8666-666666666666"),
        invoice_number="INV-0001",
        invoice_type=invoice_type,
        invoice_date=date(2026, 3, 31),
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
    assert fact.source_version == "v3"
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
    assert fact.source_version == "v2"
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
        source_version="slip-1",
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
            source_version="slip-1",
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


def test_a_document_version_below_one_is_refused():
    invoice = build_ar_invoice(version=0)
    with pytest.raises(TaxAdapterRefusal, match="version"):
        ar_invoice_line_fact(
            invoice, build_ar_line(invoice), jurisdiction_id=JURISDICTION_ID
        )


# ============================================ INBOUND: the module translation


def test_to_tax_fact_kwargs_matches_the_contract_field_list_exactly():
    invoice = build_ar_invoice()
    fact = ar_invoice_line_fact(
        invoice, build_ar_line(invoice), jurisdiction_id=JURISDICTION_ID
    )
    assert set(to_tax_fact_kwargs(fact)) == set(TAX_FACT_FIELDS)


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


def test_tax_fact_field_mirror_matches_the_installed_contract():
    """The mirror is only a mirror while the package agrees with it."""
    dotmac_tax = pytest.importorskip(
        "dotmac_tax", reason="dotmac-tax is not pinned; C1 delivers adapters only"
    )
    assert set(dotmac_tax.TaxFact.__dataclass_fields__) == set(TAX_FACT_FIELDS)


# ===================================================== OUTBOUND: determinations


def vat_component(*, sequence: int = 100, inclusive: bool = False, recoverable=True):
    tax = ngn("7500.00")
    zero = ngn("0.00")
    return TaxDeterminationComponentV1(
        component_sequence=sequence,
        tax_code_id=VAT_CODE_ID,
        rule_id=uuid.uuid4(),
        rule_version=1,
        treatment_code="standard_rated",
        calculation_base_code="source_amount",
        inclusive=inclusive,
        base_amount=ngn("100000.00"),
        tax_amount=tax,
        recoverable_amount=tax if recoverable else zero,
        non_recoverable_amount=zero if recoverable else tax,
    )


def wht_component(*, sequence: int = 200):
    """WHT 2 %: compound (on source + prior tax) and NOT recoverable."""
    tax = ngn("2150.00")
    return TaxDeterminationComponentV1(
        component_sequence=sequence,
        tax_code_id=WHT_2_CODE_ID,
        rule_id=uuid.uuid4(),
        rule_version=1,
        treatment_code="standard_rated",
        calculation_base_code="source_plus_prior_tax",
        inclusive=False,
        base_amount=ngn("107500.00"),
        tax_amount=tax,
        recoverable_amount=ngn("0.00"),
        non_recoverable_amount=tax,
    )


def exempt_component(*, sequence: int = 300):
    zero = ngn("0.00")
    return TaxDeterminationComponentV1(
        component_sequence=sequence,
        tax_code_id=STAMP_DUTY_CODE_ID,
        rule_id=uuid.uuid4(),
        rule_version=1,
        treatment_code="exempt",
        calculation_base_code="source_amount",
        inclusive=False,
        base_amount=ngn("100000.00"),
        tax_amount=zero,
        recoverable_amount=zero,
        non_recoverable_amount=zero,
    )


def build_apply(
    *,
    consequence: AccountingConsequence = AccountingConsequence.AR_OUTPUT_TAX,
    components=None,
    source: str = "100000.00",
    net: str = "100000.00",
    tax: str = "7500.00",
    gross: str = "107500.00",
    reversal: bool = False,
    fingerprint: str = FINGERPRINT,
) -> ApplyTaxDeterminationSetV1:
    return ApplyTaxDeterminationSetV1(
        organization_id=ORG_ID,
        determination_set_id=uuid.uuid4(),
        source_ref="erp:ar.invoice_line:abc",
        source_version="v3",
        source_fingerprint=fingerprint,
        occurred_on=date(2026, 3, 31),
        posting_date=date(2026, 3, 31),
        consequence=consequence,
        components=tuple(components if components is not None else [vat_component()]),
        source_amount=ngn(source),
        net_amount=ngn(net),
        tax_amount=ngn(tax),
        gross_amount=ngn(gross),
        document_id=uuid.uuid4(),
        document_type="AR_INVOICE",
        counterpart_account_id=COUNTERPART_ACCOUNT,
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
        )
    )


def project(apply: ApplyTaxDeterminationSetV1, **kwargs) -> ConsequencePosting:
    kwargs.setdefault("accounts", account_map())
    kwargs.setdefault("expected_fingerprint", apply.source_fingerprint)
    kwargs.setdefault("fiscal_period_id", FISCAL_PERIOD_ID)
    return project_determination_set(apply, **kwargs)


def test_ar_output_tax_credits_the_collected_account_and_balances():
    posting = project(build_apply())

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


def test_ap_input_tax_splits_recoverable_from_irrecoverable():
    """The one place a component yields two lines, and why the split is amounts."""
    apply = build_apply(
        consequence=AccountingConsequence.AP_INPUT_TAX,
        components=[vat_component(), wht_component()],
        tax="9650.00",
        gross="109650.00",
    )

    posting = project(apply)

    by_account = {line.account_id: line for line in posting.lines}
    assert by_account[VAT_PAID_ACCOUNT].debit_amount == Decimal("7500.00")
    assert by_account[WHT_EXPENSE_ACCOUNT].debit_amount == Decimal("2150.00")
    assert by_account[COUNTERPART_ACCOUNT].credit_amount == Decimal("9650.00")
    assert posting.total_debit == posting.total_credit == Decimal("9650.00")


def test_reversal_flips_every_line_and_still_balances():
    posting = project(build_apply(reversal=True))

    tax_line, counterpart = posting.lines
    assert tax_line.debit_amount == Decimal("7500.00")
    assert tax_line.credit_amount == Decimal("0")
    assert counterpart.credit_amount == Decimal("7500.00")
    assert posting.total_debit == posting.total_credit


def test_a_missing_account_mapping_refuses_the_whole_set():
    with pytest.raises(TaxAdapterRefusal, match="no collected account mapped"):
        project(build_apply(), accounts=account_map(vat_collected=False))


def test_an_unmapped_tax_code_refuses_the_whole_set():
    empty = TaxAccountMap(entries=())
    with pytest.raises(TaxAdapterRefusal, match="no ERP account mapping"):
        project(build_apply(), accounts=empty)


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
    apply = build_apply()
    with pytest.raises(TaxAdapterRefusal, match="fingerprint changed"):
        project(apply, expected_fingerprint="0" * 64)


def test_an_open_period_cannot_be_asserted_by_omission():
    with pytest.raises(TaxAdapterRefusal, match="open fiscal period id is required"):
        project_determination_set(
            build_apply(),
            accounts=account_map(),
            expected_fingerprint=FINGERPRINT,
            fiscal_period_id=None,  # type: ignore[arg-type]
        )


# ================================== OUTBOUND: the inclusive/compound arithmetic


def test_an_inclusive_set_requires_source_to_equal_gross():
    """`VAT-7.5 (inclusive)` becomes rule-level treatment, and the arithmetic
    that distinguishes it is re-derived here rather than trusted."""
    apply = build_apply(
        components=[vat_component(inclusive=True)],
        source="107500.00",
        net="100000.00",
        tax="7500.00",
        gross="107500.00",
    )
    posting = project(apply)
    assert posting.total_debit == posting.total_credit == Decimal("7500.00")


def test_an_inclusive_set_whose_source_equals_net_is_refused():
    apply = build_apply(components=[vat_component(inclusive=True)])
    with pytest.raises(TaxAdapterRefusal, match="inclusive set"):
        project(apply)


def test_an_exclusive_set_whose_source_equals_gross_is_refused():
    apply = build_apply(source="107500.00")
    with pytest.raises(TaxAdapterRefusal, match="exclusive set"):
        project(apply)


def test_an_inclusive_component_beside_any_other_is_refused():
    """Mirrors the a2 candidate's own refusal: the source amount is ambiguous."""
    apply = build_apply(
        components=[vat_component(inclusive=True), wht_component()],
        source="107500.00",
        net="100000.00",
        tax="9650.00",
        gross="109650.00",
    )
    with pytest.raises(TaxAdapterRefusal, match="cannot be combined"):
        project(apply)


def test_components_out_of_calculation_order_are_refused():
    with pytest.raises(TaxAdapterRefusal, match="strictly increasing"):
        build_apply(
            components=[wht_component(sequence=100), vat_component(sequence=50)],
            tax="9650.00",
            gross="109650.00",
        )


def test_a_component_whose_recovery_split_does_not_add_up_is_refused():
    with pytest.raises(TaxAdapterRefusal, match="recovery split"):
        TaxDeterminationComponentV1(
            component_sequence=100,
            tax_code_id=VAT_CODE_ID,
            rule_id=uuid.uuid4(),
            rule_version=1,
            treatment_code="standard_rated",
            calculation_base_code="source_amount",
            inclusive=False,
            base_amount=ngn("100000.00"),
            tax_amount=ngn("7500.00"),
            recoverable_amount=ngn("7500.00"),
            non_recoverable_amount=ngn("0.01"),
        )


def test_components_that_do_not_total_the_set_are_refused():
    with pytest.raises(TaxAdapterRefusal, match="components total"):
        build_apply(tax="9999.00", gross="109999.00")


def test_net_plus_tax_must_equal_gross():
    with pytest.raises(TaxAdapterRefusal, match="does not equal gross"):
        build_apply(gross="107500.01")


# ================================================ OUTBOUND: zero treatments


def test_a_zero_treatment_component_is_carried_not_dropped():
    apply = build_apply(components=[vat_component(), exempt_component()])

    posting = project(apply)

    assert len(posting.lines) == 2, "the exempt component produces no journal line"
    assert len(posting.reportable_zero_components) == 1
    assert posting.reportable_zero_components[0].treatment_code == "exempt"


def test_an_all_zero_set_names_the_report_consequence_and_refuses_to_post():
    apply = build_apply(components=[exempt_component()], tax="0.00", gross="100000.00")
    with pytest.raises(TaxAdapterRefusal, match="return-box consequence"):
        project(apply)


def test_a_standard_rated_component_holding_no_tax_is_a_defect():
    zero = ngn("0.00")
    component = TaxDeterminationComponentV1(
        component_sequence=100,
        tax_code_id=VAT_CODE_ID,
        rule_id=uuid.uuid4(),
        rule_version=1,
        treatment_code="standard_rated",
        calculation_base_code="source_amount",
        inclusive=False,
        base_amount=ngn("100000.00"),
        tax_amount=zero,
        recoverable_amount=zero,
        non_recoverable_amount=zero,
    )
    apply = build_apply(components=[component], tax="0.00", gross="100000.00")
    with pytest.raises(TaxAdapterRefusal, match="determination defect"):
        project(apply)


def test_a_zero_treatment_holding_money_is_refused_at_the_contract():
    with pytest.raises(TaxAdapterRefusal, match="a zero treatment holding money"):
        TaxDeterminationComponentV1(
            component_sequence=100,
            tax_code_id=VAT_CODE_ID,
            rule_id=uuid.uuid4(),
            rule_version=1,
            treatment_code="exempt",
            calculation_base_code="source_amount",
            inclusive=False,
            base_amount=ngn("100000.00"),
            tax_amount=ngn("1.00"),
            recoverable_amount=ngn("1.00"),
            non_recoverable_amount=ngn("0.00"),
        )


# ============================================ OUTBOUND: the accounting owner


def test_the_projection_renders_into_the_accounting_owners_own_input_type():
    posting = project(build_apply())

    journal = posting.to_journal_input()

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
    """The outbound contract mirrors determinations; it imports no module type."""
    import app.services.finance.tax.adoption.outbound as outbound

    source = (outbound.__file__ or "").replace(".pyc", ".py")
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "import dotmac_tax" not in text
    assert "from dotmac_tax" not in text

    component_fields = set(TaxDeterminationComponentV1.__dataclass_fields__)
    assert not component_fields & {
        "account_id",
        "journal_entry_id",
        "fiscal_period_id",
        "debit_amount",
        "credit_amount",
    }
