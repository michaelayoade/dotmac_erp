"""WHT accounting contract shared by ERP and the Sub importer."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.models.finance.ar.customer_payment import CustomerPayment, PaymentStatus
from app.models.finance.tax.tax_code import TaxCode, TaxType
from app.services.finance.ar.posting.payment import post_payment
from app.services.finance.ar.posting.result import ARPostingResult


def test_idempotent_payment_posting_books_net_cash_wht_and_gross_ar(
    monkeypatch,
) -> None:
    org_id = uuid4()
    payment_id = uuid4()
    customer_id = uuid4()
    wht_code_id = uuid4()
    wht_receivable_id = uuid4()
    bank_gl_id = uuid4()
    payment = SimpleNamespace(
        payment_id=payment_id,
        organization_id=org_id,
        customer_id=customer_id,
        status=PaymentStatus.CLEARED,
        amount=Decimal("100.00"),
        gross_amount=Decimal("107.50"),
        wht_amount=Decimal("7.50"),
        wht_code_id=wht_code_id,
        wht_certificate_number="CERT-42",
        payment_date=date(2026, 7, 1),
        exchange_rate=Decimal("1"),
        bank_account_id=uuid4(),
        payment_number="PMT-1",
        reference="SUB-PMT-1",
        currency_code="NGN",
        correlation_id=None,
    )
    customer = SimpleNamespace(
        customer_id=customer_id,
        legal_name="Example Customer",
        ar_control_account_id=uuid4(),
        tax_identification_number=None,
    )
    wht_code = SimpleNamespace(
        tax_code_id=wht_code_id,
        organization_id=org_id,
        tax_type=TaxType.WITHHOLDING,
        tax_paid_account_id=wht_receivable_id,
        jurisdiction_id=uuid4(),
        tax_rate=Decimal("0.075"),
        tax_return_box=None,
    )
    db = MagicMock()
    db.scalar.return_value = None

    def get(model, record_id):
        if model is CustomerPayment and record_id == payment_id:
            return payment
        if getattr(model, "__name__", "") == "Customer" and record_id == customer_id:
            return customer
        if model is TaxCode and record_id == wht_code_id:
            return wht_code
        return None

    db.get.side_effect = get
    captured = {}
    journal = SimpleNamespace(journal_entry_id=uuid4())

    def create_and_post(_db, _org, journal_input, _user, **_kwargs):
        captured["input"] = journal_input
        return journal, ARPostingResult(
            success=True, journal_entry_id=journal.journal_entry_id
        )

    monkeypatch.setattr(
        "app.services.finance.ar.posting.payment._resolve_bank_gl_account_id",
        lambda *_args, **_kwargs: bank_gl_id,
    )
    monkeypatch.setattr(
        "app.services.finance.ar.posting.payment.BasePostingAdapter.create_approve_and_post_journal",
        create_and_post,
    )
    monkeypatch.setattr(
        "app.services.finance.ar.posting.payment.post_vat_reclass_for_payment",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.finance.gl.period_guard.PeriodGuardService.get_period_for_date",
        lambda *_args, **_kwargs: SimpleNamespace(fiscal_period_id=uuid4()),
    )
    monkeypatch.setattr(
        "app.services.finance.tax.tax_transaction.tax_transaction_service.create_transaction",
        lambda **_kwargs: SimpleNamespace(),
    )

    result = post_payment(
        db,
        org_id,
        payment_id,
        payment.payment_date,
        uuid4(),
    )

    assert result.success
    lines = captured["input"].lines
    assert [(line.debit_amount, line.credit_amount) for line in lines] == [
        (Decimal("100.00"), Decimal("0")),
        (Decimal("7.50"), Decimal("0")),
        (Decimal("0"), Decimal("107.50")),
    ]
    assert lines[0].account_id == bank_gl_id
    assert lines[1].account_id == wht_receivable_id
    assert lines[2].account_id == customer.ar_control_account_id


def test_wht_payment_fails_closed_without_erp_receivable_mapping(monkeypatch) -> None:
    org_id = uuid4()
    payment_id = uuid4()
    customer_id = uuid4()
    payment = SimpleNamespace(
        payment_id=payment_id,
        organization_id=org_id,
        customer_id=customer_id,
        status=PaymentStatus.CLEARED,
        amount=Decimal("95"),
        gross_amount=Decimal("100"),
        wht_amount=Decimal("5"),
        wht_code_id=uuid4(),
        payment_date=date(2026, 7, 1),
        exchange_rate=Decimal("1"),
        bank_account_id=uuid4(),
        reference="SUB-PMT-2",
    )
    customer = SimpleNamespace(customer_id=customer_id, legal_name="Customer")
    db = MagicMock()
    db.scalar.return_value = None
    db.get.side_effect = lambda model, record_id: (
        payment
        if model is CustomerPayment
        else customer
        if getattr(model, "__name__", "") == "Customer"
        else None
    )
    monkeypatch.setattr(
        "app.services.finance.ar.posting.payment._resolve_bank_gl_account_id",
        lambda *_args, **_kwargs: uuid4(),
    )

    result = post_payment(db, org_id, payment_id, payment.payment_date, uuid4())

    assert not result.success
    assert "receivable account" in result.message
