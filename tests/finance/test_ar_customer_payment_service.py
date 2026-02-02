from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.finance.ar.customer_payment import PaymentMethod, PaymentStatus
from app.models.finance.ar.invoice import InvoiceStatus
from app.services.finance.ar.customer_payment import (
    CustomerPaymentInput,
    CustomerPaymentService,
    PaymentAllocationInput,
)


def _make_customer(org_id, active=True):
    return SimpleNamespace(
        customer_id=uuid4(),
        organization_id=org_id,
        is_active=active,
        legal_name="ACME",
        ar_control_account_id=uuid4(),
        tax_identification_number="TIN",
    )


def test_create_payment_allocation_exceeds_amount():
    db = MagicMock()
    org_id = uuid4()
    customer = _make_customer(org_id)
    db.get.return_value = customer

    with pytest.raises(HTTPException) as excinfo:
        CustomerPaymentService.create_payment(
            db,
            org_id,
            CustomerPaymentInput(
                customer_id=customer.customer_id,
                payment_date=date.today(),
                payment_method=PaymentMethod.CARD,
                currency_code="NGN",
                amount=Decimal("50.00"),
                allocations=[PaymentAllocationInput(invoice_id=uuid4(), amount=Decimal("60.00"))],
            ),
            created_by_user_id=uuid4(),
        )

    assert excinfo.value.status_code == 400


def test_create_payment_wht_mismatch():
    db = MagicMock()
    org_id = uuid4()
    customer = _make_customer(org_id)
    db.get.return_value = customer

    with pytest.raises(HTTPException) as excinfo:
        CustomerPaymentService.create_payment(
            db,
            org_id,
            CustomerPaymentInput(
                customer_id=customer.customer_id,
                payment_date=date.today(),
                payment_method=PaymentMethod.CARD,
                currency_code="NGN",
                amount=Decimal("90.00"),
                gross_amount=Decimal("100.00"),
                wht_amount=Decimal("5.00"),
            ),
            created_by_user_id=uuid4(),
        )

    assert excinfo.value.status_code == 400


def test_create_payment_calculates_gross_from_wht():
    db = MagicMock()
    org_id = uuid4()
    customer = _make_customer(org_id)
    db.get.return_value = customer

    with (
        patch("app.services.finance.ar.customer_payment.SequenceService.get_next_number", return_value="RCPT-1"),
    ):
        payment = CustomerPaymentService.create_payment(
            db,
            org_id,
            CustomerPaymentInput(
                customer_id=customer.customer_id,
                payment_date=date.today(),
                payment_method=PaymentMethod.CARD,
                currency_code="NGN",
                amount=Decimal("90.00"),
                wht_amount=Decimal("10.00"),
            ),
            created_by_user_id=uuid4(),
        )

    assert payment.gross_amount == Decimal("100.00")
    assert payment.amount == Decimal("90.00")


def test_post_payment_requires_bank_account():
    db = MagicMock()
    org_id = uuid4()
    payment = SimpleNamespace(
        payment_id=uuid4(),
        organization_id=org_id,
        status=PaymentStatus.PENDING,
        bank_account_id=None,
    )
    db.get.return_value = payment

    with pytest.raises(HTTPException) as excinfo:
        CustomerPaymentService.post_payment(db, org_id, payment.payment_id, posted_by_user_id=uuid4())
    assert excinfo.value.status_code == 400


def test_post_payment_wht_requires_receivable_account():
    db = MagicMock()
    org_id = uuid4()
    payment = SimpleNamespace(
        payment_id=uuid4(),
        organization_id=org_id,
        status=PaymentStatus.PENDING,
        bank_account_id=uuid4(),
        customer_id=uuid4(),
        exchange_rate=Decimal("1.0"),
        amount=Decimal("90.00"),
        gross_amount=Decimal("100.00"),
        wht_amount=Decimal("10.00"),
        wht_code_id=uuid4(),
        wht_certificate_number=None,
        payment_date=date.today(),
        reference=None,
        payment_number="RCPT-1",
        currency_code="NGN",
        correlation_id="c",
    )
    customer = _make_customer(org_id)
    customer.customer_id = payment.customer_id
    db.get.side_effect = [payment, customer, None]  # payment, customer, tax code (None)

    with pytest.raises(HTTPException) as excinfo:
        CustomerPaymentService.post_payment(db, org_id, payment.payment_id, posted_by_user_id=uuid4())
    assert excinfo.value.status_code == 400


def test_post_payment_success_without_wht():
    db = MagicMock()
    org_id = uuid4()
    payment = SimpleNamespace(
        payment_id=uuid4(),
        organization_id=org_id,
        status=PaymentStatus.PENDING,
        bank_account_id=uuid4(),
        customer_id=uuid4(),
        exchange_rate=Decimal("1.0"),
        amount=Decimal("100.00"),
        gross_amount=Decimal("100.00"),
        wht_amount=Decimal("0"),
        wht_code_id=None,
        wht_certificate_number=None,
        payment_date=date.today(),
        reference=None,
        payment_number="RCPT-1",
        currency_code="NGN",
        correlation_id="c",
    )
    customer = _make_customer(org_id)
    customer.customer_id = payment.customer_id

    db.get.side_effect = [payment, customer]
    db.query.return_value.filter.return_value.all.return_value = []

    journal = SimpleNamespace(journal_entry_id=uuid4())
    posting_result = SimpleNamespace(success=True, posting_batch_id=uuid4(), message=None)

    with (
        patch("app.services.finance.gl.journal.JournalService.create_journal", return_value=journal),
        patch("app.services.finance.gl.journal.JournalService.submit_journal", return_value=None),
        patch("app.services.finance.gl.journal.JournalService.approve_journal", return_value=None),
        patch("app.services.finance.gl.ledger_posting.LedgerPostingService.post_journal_entry", return_value=posting_result),
    ):
        result = CustomerPaymentService.post_payment(
            db,
            org_id,
            payment.payment_id,
            posted_by_user_id=uuid4(),
        )

    assert result.status == PaymentStatus.CLEARED
    assert result.journal_entry_id == journal.journal_entry_id


def test_void_and_bounce_reverse_allocations():
    db = MagicMock()
    org_id = uuid4()
    payment = SimpleNamespace(
        payment_id=uuid4(),
        organization_id=org_id,
        status=PaymentStatus.CLEARED,
    )
    invoice = SimpleNamespace(amount_paid=Decimal("50.00"), total_amount=Decimal("100.00"), status=InvoiceStatus.PAID)
    allocation = SimpleNamespace(invoice_id=uuid4(), allocated_amount=Decimal("50.00"))

    def _get(model, _id):
        if model.__name__ == "CustomerPayment":
            return payment
        if model.__name__ == "Invoice":
            return invoice
        return None

    db.get.side_effect = _get
    db.query.return_value.filter.return_value.all.return_value = [allocation]

    voided = CustomerPaymentService.void_payment(db, org_id, payment.payment_id, voided_by_user_id=uuid4(), reason="err")
    assert voided.status == PaymentStatus.VOID
    assert invoice.amount_paid == Decimal("0")
    assert invoice.status == InvoiceStatus.POSTED

    payment.status = PaymentStatus.CLEARED
    invoice.amount_paid = Decimal("50.00")
    bounced = CustomerPaymentService.mark_bounced(db, org_id, payment.payment_id, reason="nsf")
    assert bounced.status == PaymentStatus.BOUNCED
