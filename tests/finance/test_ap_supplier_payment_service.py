from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.models.finance.ap.supplier_invoice import SupplierInvoiceStatus
from app.models.finance.ap.supplier_payment import APPaymentMethod, APPaymentStatus
from app.models.finance.tax.tax_code import TaxType
from app.services.common import NotFoundError, ValidationError
from app.services.finance.ap.supplier_payment import (
    PaymentAllocationInput,
    SupplierPaymentInput,
    SupplierPaymentService,
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

    with pytest.raises(NotFoundError):
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
    with pytest.raises(ValidationError):
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

    with pytest.raises(ValidationError, match="WHT tax code"):
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


def test_create_payment_rejects_non_withholding_tax_code():
    db = MagicMock()
    org_id = uuid4()
    supplier = _make_supplier(org_id, active=True, with_wht=False)
    non_wht_code = SimpleNamespace(
        tax_code_id=uuid4(),
        organization_id=org_id,
        tax_type=TaxType.VAT,
    )

    def _get(model, _id):
        if model.__name__ == "Supplier":
            return supplier
        if model.__name__ == "TaxCode":
            return non_wht_code
        return None

    db.get.side_effect = _get

    with pytest.raises(ValidationError, match="WITHHOLDING"):
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
                wht_code_id=non_wht_code.tax_code_id,
            ),
            created_by_user_id=uuid4(),
        )


def test_create_payment_allocation_checks():
    db = MagicMock()
    org_id = uuid4()
    supplier = _make_supplier(org_id, active=True)
    db.get.return_value = supplier

    with pytest.raises(ValidationError, match="exceeds"):
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
                allocations=[
                    PaymentAllocationInput(invoice_id=uuid4(), amount=Decimal("150.00"))
                ],
            ),
            created_by_user_id=uuid4(),
        )


def test_create_payment_inherits_planned_invoice_wht():
    db = MagicMock()
    org_id = uuid4()
    supplier = _make_supplier(org_id, active=True, with_wht=False)
    invoice_id = uuid4()
    wht_code_id = uuid4()
    invoice = SimpleNamespace(
        invoice_id=invoice_id,
        invoice_number="SINV-WHT",
        organization_id=org_id,
        supplier_id=supplier.supplier_id,
        status=SupplierInvoiceStatus.POSTED,
        total_amount=Decimal("1075.00"),
        balance_due=Decimal("1075.00"),
        withholding_tax_amount=Decimal("50.00"),
        withholding_tax_code_id=wht_code_id,
    )
    wht_code = SimpleNamespace(
        tax_code_id=wht_code_id,
        organization_id=org_id,
        tax_type=TaxType.WITHHOLDING,
    )

    def get_record(model, record_id):
        if record_id == supplier.supplier_id:
            return supplier
        if record_id == invoice_id:
            return invoice
        if model.__name__ == "TaxCode" and record_id == wht_code_id:
            return wht_code
        return None

    db.get.side_effect = get_record

    with (
        patch(
            "app.services.finance.ap.supplier_payment.SequenceService.get_next_number",
            return_value="PAY-WHT-001",
        ),
        patch("app.services.finance.ap.supplier_payment.fire_audit_event"),
    ):
        payment = SupplierPaymentService.create_payment(
            db,
            org_id,
            SupplierPaymentInput(
                supplier_id=supplier.supplier_id,
                payment_date=date.today(),
                payment_method=APPaymentMethod.BANK_TRANSFER,
                currency_code="NGN",
                amount=Decimal("1075.00"),
                bank_account_id=uuid4(),
                allocations=[
                    PaymentAllocationInput(
                        invoice_id=invoice_id,
                        amount=Decimal("1075.00"),
                    )
                ],
            ),
            created_by_user_id=uuid4(),
        )

    assert payment.gross_amount == Decimal("1075.00")
    assert payment.amount == Decimal("1025.00")
    assert payment.withholding_tax_amount == Decimal("50.00")
    assert payment.withholding_tax_code_id == wht_code_id


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

    with (
        patch(
            "app.services.feature_flags.is_feature_enabled",
            return_value=True,
        ),
        pytest.raises(ValidationError, match="[Ss]egregation"),
    ):
        SupplierPaymentService.approve_payment(
            db, org_id, payment.payment_id, payment.created_by_user_id
        )

    approved = SupplierPaymentService.approve_payment(
        db, org_id, payment.payment_id, approved_by_user_id=uuid4()
    )
    assert approved.status == APPaymentStatus.APPROVED

    payment.status = APPaymentStatus.APPROVED
    with patch(
        "app.services.finance.ap.ap_posting_adapter.APPostingAdapter.post_payment"
    ) as post_payment:
        post_payment.return_value = SimpleNamespace(
            success=True, journal_entry_id=uuid4(), posting_batch_id=uuid4()
        )
        db.scalars.return_value.all.return_value = []
        posted = SupplierPaymentService.post_payment(
            db, org_id, payment.payment_id, posted_by_user_id=uuid4()
        )
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
    invoice = SimpleNamespace(
        amount_paid=Decimal("0"),
        total_amount=Decimal("100.00"),
        status=SupplierInvoiceStatus.POSTED,
    )
    allocation = SimpleNamespace(invoice_id=uuid4(), allocated_amount=Decimal("50.00"))

    def _get(model, _id):
        if model.__name__ == "SupplierPayment":
            return payment
        if model.__name__ == "SupplierInvoice":
            return invoice
        return None

    db.get.side_effect = _get
    db.scalars.return_value.all.return_value = [allocation]

    with patch(
        "app.services.finance.ap.ap_posting_adapter.APPostingAdapter.post_payment"
    ) as post_payment:
        post_payment.return_value = SimpleNamespace(
            success=True, journal_entry_id=uuid4(), posting_batch_id=uuid4()
        )
        posted = SupplierPaymentService.post_payment(
            db, org_id, payment.payment_id, posted_by_user_id=uuid4()
        )
        assert posted.status == APPaymentStatus.SENT
        assert invoice.amount_paid == Decimal("50.00")
        assert invoice.status == SupplierInvoiceStatus.PARTIALLY_PAID

    payment.status = APPaymentStatus.SENT
    voided = SupplierPaymentService.void_payment(
        db, org_id, payment.payment_id, voided_by_user_id=uuid4(), reason="err"
    )
    assert voided.status == APPaymentStatus.VOID
    assert invoice.amount_paid == Decimal("0")


def test_post_payment_does_not_auto_receipt_before_full_payment():
    db = MagicMock()
    org_id = uuid4()
    payment = SimpleNamespace(
        payment_id=uuid4(),
        organization_id=org_id,
        status=APPaymentStatus.APPROVED,
        payment_date=date.today(),
    )
    invoice = SimpleNamespace(
        invoice_id=uuid4(),
        amount_paid=Decimal("0"),
        total_amount=Decimal("100.00"),
        status=SupplierInvoiceStatus.POSTED,
    )
    allocation = SimpleNamespace(
        invoice_id=invoice.invoice_id, allocated_amount=Decimal("50.00")
    )

    def _get(model, _id):
        if model.__name__ == "SupplierPayment":
            return payment
        if model.__name__ == "SupplierInvoice":
            return invoice
        return None

    db.get.side_effect = _get
    db.scalars.return_value.all.return_value = [allocation]

    with (
        patch(
            "app.services.finance.ap.ap_posting_adapter.APPostingAdapter.post_payment"
        ) as post_payment,
        patch(
            "app.services.finance.ap.auto_inventory_receipt.ap_invoice_auto_receipt_service.create_for_invoice"
        ) as create_auto_receipt,
    ):
        post_payment.return_value = SimpleNamespace(
            success=True, journal_entry_id=uuid4(), posting_batch_id=uuid4()
        )
        SupplierPaymentService.post_payment(
            db, org_id, payment.payment_id, posted_by_user_id=uuid4()
        )

    assert invoice.status == SupplierInvoiceStatus.PARTIALLY_PAID
    create_auto_receipt.assert_not_called()


def test_post_payment_does_not_auto_receipt_after_full_payment():
    db = MagicMock()
    org_id = uuid4()
    user_id = uuid4()
    payment = SimpleNamespace(
        payment_id=uuid4(),
        organization_id=org_id,
        status=APPaymentStatus.APPROVED,
        payment_date=date.today(),
    )
    invoice = SimpleNamespace(
        invoice_id=uuid4(),
        amount_paid=Decimal("25.00"),
        total_amount=Decimal("100.00"),
        status=SupplierInvoiceStatus.PARTIALLY_PAID,
    )
    allocation = SimpleNamespace(
        invoice_id=invoice.invoice_id, allocated_amount=Decimal("75.00")
    )

    def _get(model, _id):
        if model.__name__ == "SupplierPayment":
            return payment
        if model.__name__ == "SupplierInvoice":
            return invoice
        return None

    db.get.side_effect = _get
    db.scalars.return_value.all.return_value = [allocation]

    with (
        patch(
            "app.services.finance.ap.ap_posting_adapter.APPostingAdapter.post_payment"
        ) as post_payment,
        patch(
            "app.services.finance.ap.auto_inventory_receipt.ap_invoice_auto_receipt_service.create_for_invoice"
        ) as create_auto_receipt,
    ):
        post_payment.return_value = SimpleNamespace(
            success=True, journal_entry_id=uuid4(), posting_batch_id=uuid4()
        )
        SupplierPaymentService.post_payment(
            db, org_id, payment.payment_id, posted_by_user_id=user_id
        )

    assert invoice.status == SupplierInvoiceStatus.PAID
    create_auto_receipt.assert_not_called()


def test_mark_cleared_and_list():
    db = MagicMock()
    org_id = uuid4()
    payment = SimpleNamespace(
        payment_id=uuid4(), organization_id=org_id, status=APPaymentStatus.SENT
    )
    db.get.return_value = payment
    cleared = SupplierPaymentService.mark_cleared(
        db, org_id, payment.payment_id, cleared_date=date.today()
    )
    assert cleared.status == APPaymentStatus.CLEARED

    db.scalars.return_value.all.return_value = []
    SupplierPaymentService.list(db, organization_id=str(org_id))
