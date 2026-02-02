from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.finance.ap.supplier_payment import APPaymentMethod, APPaymentStatus
from app.models.finance.ap.supplier_invoice import SupplierInvoiceStatus
from app.services.finance.ap.supplier_payment import (
    SupplierPaymentInput,
    SupplierPaymentService,
    PaymentAllocationInput,
)


def _make_supplier(org_id, active=True, with_wht=False):
    return SimpleNamespace(
        supplier_id=uuid4(),
        organization_id=org_id,
        is_active=active,
        withholding_tax_applicable=with_wht,
        withholding_tax_code_id=uuid4() if with_wht else None,
    )


def test_create_payment_requires_supplier_and_valid_amounts():
    db = MagicMock()
    org_id = uuid4()
    db.get.return_value = None

    with pytest.raises(HTTPException):
        SupplierPaymentService.create_payment(
            db,
            org_id,
            SupplierPaymentInput(
                supplier_id=uuid4(),
                payment_date=date.today(),
                payment_method=APPaymentMethod.BANK_TRANSFER,
                currency_code="NGN",
                amount=Decimal("100.00"),
                bank_account_id=uuid4(),
            ),
            created_by_user_id=uuid4(),
        )

    supplier = _make_supplier(org_id, active=False)
    db.get.return_value = supplier
    with pytest.raises(HTTPException):
        SupplierPaymentService.create_payment(
            db,
            org_id,
            SupplierPaymentInput(
                supplier_id=supplier.supplier_id,
                payment_date=date.today(),
                payment_method=APPaymentMethod.BANK_TRANSFER,
                currency_code="NGN",
                amount=Decimal("100.00"),
                bank_account_id=uuid4(),
            ),
            created_by_user_id=uuid4(),
        )


def test_create_payment_wht_requires_code():
    db = MagicMock()
    org_id = uuid4()
    supplier = _make_supplier(org_id, active=True, with_wht=False)
    db.get.return_value = supplier

    with pytest.raises(HTTPException) as excinfo:
        SupplierPaymentService.create_payment(
            db,
            org_id,
            SupplierPaymentInput(
                supplier_id=supplier.supplier_id,
                payment_date=date.today(),
                payment_method=APPaymentMethod.BANK_TRANSFER,
                currency_code="NGN",
                amount=Decimal("90.00"),
                bank_account_id=uuid4(),
                wht_amount=Decimal("10.00"),
            ),
            created_by_user_id=uuid4(),
        )
    assert excinfo.value.status_code == 400


def test_create_payment_allocation_checks():
    db = MagicMock()
    org_id = uuid4()
    supplier = _make_supplier(org_id, active=True)
    db.get.return_value = supplier

    with pytest.raises(HTTPException):
        SupplierPaymentService.create_payment(
            db,
            org_id,
            SupplierPaymentInput(
                supplier_id=supplier.supplier_id,
                payment_date=date.today(),
                payment_method=APPaymentMethod.BANK_TRANSFER,
                currency_code="NGN",
                amount=Decimal("100.00"),
                bank_account_id=uuid4(),
                allocations=[PaymentAllocationInput(invoice_id=uuid4(), amount=Decimal("150.00"))],
            ),
            created_by_user_id=uuid4(),
        )


def test_approve_and_post_payment():
    db = MagicMock()
    org_id = uuid4()
    payment = SimpleNamespace(
        payment_id=uuid4(),
        organization_id=org_id,
        status=APPaymentStatus.DRAFT,
        created_by_user_id=uuid4(),
        payment_date=date.today(),
    )
    db.get.return_value = payment

    with pytest.raises(HTTPException):
        SupplierPaymentService.approve_payment(db, org_id, payment.payment_id, payment.created_by_user_id)

    approved = SupplierPaymentService.approve_payment(db, org_id, payment.payment_id, approved_by_user_id=uuid4())
    assert approved.status == APPaymentStatus.APPROVED

    payment.status = APPaymentStatus.APPROVED
    with patch("app.services.finance.ap.ap_posting_adapter.APPostingAdapter.post_payment") as post_payment:
        post_payment.return_value = SimpleNamespace(success=True, journal_entry_id=uuid4(), posting_batch_id=uuid4())
        db.query.return_value.filter.return_value.all.return_value = []
        posted = SupplierPaymentService.post_payment(db, org_id, payment.payment_id, posted_by_user_id=uuid4())
        assert posted.status == APPaymentStatus.SENT


def test_post_payment_applies_allocations_and_void():
    db = MagicMock()
    org_id = uuid4()
    payment = SimpleNamespace(
        payment_id=uuid4(),
        organization_id=org_id,
        status=APPaymentStatus.APPROVED,
        payment_date=date.today(),
    )
    invoice = SimpleNamespace(amount_paid=Decimal("0"), total_amount=Decimal("100.00"), status=SupplierInvoiceStatus.POSTED)
    allocation = SimpleNamespace(invoice_id=uuid4(), allocated_amount=Decimal("50.00"))

    def _get(model, _id):
        if model.__name__ == "SupplierPayment":
            return payment
        if model.__name__ == "SupplierInvoice":
            return invoice
        return None

    db.get.side_effect = _get
    db.query.return_value.filter.return_value.all.return_value = [allocation]

    with patch("app.services.finance.ap.ap_posting_adapter.APPostingAdapter.post_payment") as post_payment:
        post_payment.return_value = SimpleNamespace(success=True, journal_entry_id=uuid4(), posting_batch_id=uuid4())
        posted = SupplierPaymentService.post_payment(db, org_id, payment.payment_id, posted_by_user_id=uuid4())
        assert posted.status == APPaymentStatus.SENT
        assert invoice.amount_paid == Decimal("50.00")
        assert invoice.status == SupplierInvoiceStatus.PARTIALLY_PAID

    payment.status = APPaymentStatus.SENT
    voided = SupplierPaymentService.void_payment(db, org_id, payment.payment_id, voided_by_user_id=uuid4(), reason="err")
    assert voided.status == APPaymentStatus.VOID
    assert invoice.amount_paid == Decimal("0")


def test_mark_cleared_and_list():
    db = MagicMock()
    org_id = uuid4()
    payment = SimpleNamespace(payment_id=uuid4(), organization_id=org_id, status=APPaymentStatus.SENT)
    db.get.return_value = payment
    cleared = SupplierPaymentService.mark_cleared(db, org_id, payment.payment_id, cleared_date=date.today())
    assert cleared.status == APPaymentStatus.CLEARED

    query = MagicMock()
    db.query.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit.return_value.offset.return_value.all.return_value = []
    SupplierPaymentService.list(db, organization_id=str(org_id))
