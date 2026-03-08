from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.finance.ap.payment_batch import APBatchStatus
from app.models.finance.ap.supplier_invoice import SupplierInvoiceStatus
from app.models.finance.ap.supplier_payment import APPaymentStatus
from app.services.finance.ap.payment_batch import (
    BatchPaymentItem,
    PaymentBatchInput,
    PaymentBatchService,
)


def test_create_batch_requires_payments():
    db = MagicMock()
    org_id = uuid4()

    with pytest.raises(HTTPException):
        PaymentBatchService.create_batch(
            db,
            org_id,
            PaymentBatchInput(
                batch_date=date.today(),
                payment_method="ACH",
                bank_account_id=uuid4(),
                currency_code="NGN",
                payments=[],
            ),
            created_by_user_id=uuid4(),
        )


def test_create_batch_sets_totals():
    db = MagicMock()
    org_id = uuid4()
    with patch(
        "app.services.finance.ap.payment_batch.SequenceService.get_next_number",
        return_value="001",
    ):
        batch = PaymentBatchService.create_batch(
            db,
            org_id,
            PaymentBatchInput(
                batch_date=date.today(),
                payment_method="ACH",
                bank_account_id=uuid4(),
                currency_code="NGN",
                payments=[
                    BatchPaymentItem(supplier_id=uuid4(), amount=Decimal("50.00")),
                    BatchPaymentItem(supplier_id=uuid4(), amount=Decimal("25.00")),
                ],
            ),
            created_by_user_id=uuid4(),
        )

    assert batch.total_payments == 2
    assert batch.total_amount == Decimal("75.00")
    assert batch.batch_number.startswith("BATCH-")


def test_add_and_remove_payment_from_batch():
    db = MagicMock()
    org_id = uuid4()
    batch = SimpleNamespace(
        batch_id=uuid4(),
        organization_id=org_id,
        status=APBatchStatus.DRAFT,
        total_payments=0,
        total_amount=Decimal("0"),
    )
    payment = SimpleNamespace(
        payment_id=uuid4(),
        organization_id=org_id,
        status=APPaymentStatus.DRAFT,
        payment_batch_id=None,
        amount=Decimal("40.00"),
    )

    db.scalars.return_value.first.side_effect = [batch, payment]

    updated = PaymentBatchService.add_payment_to_batch(
        db, org_id, batch.batch_id, payment.payment_id
    )
    assert updated.total_payments == 1
    assert updated.total_amount == Decimal("40.00")
    assert payment.payment_batch_id == batch.batch_id

    db.scalars.return_value.first.side_effect = [batch, payment]
    removed = PaymentBatchService.remove_payment_from_batch(
        db, org_id, batch.batch_id, payment.payment_id
    )
    assert removed.total_payments == 0
    assert removed.total_amount == Decimal("0.00")
    assert payment.payment_batch_id is None


def test_approve_and_process_batch():
    db = MagicMock()
    org_id = uuid4()
    batch = SimpleNamespace(
        batch_id=uuid4(),
        organization_id=org_id,
        status=APBatchStatus.DRAFT,
        created_by_user_id=uuid4(),
    )
    payment = SimpleNamespace(payment_id=uuid4(), status=APPaymentStatus.DRAFT)

    db.scalars.return_value.first.return_value = batch
    db.scalar.return_value = 1
    db.scalars.return_value.all.return_value = [payment]

    with pytest.raises(HTTPException):
        PaymentBatchService.approve_batch(
            db, org_id, batch.batch_id, batch.created_by_user_id
        )

    approved = PaymentBatchService.approve_batch(
        db, org_id, batch.batch_id, approved_by_user_id=uuid4()
    )
    assert approved.status == APBatchStatus.APPROVED
    assert payment.status == APPaymentStatus.APPROVED

    batch.status = APBatchStatus.APPROVED
    payment.status = APPaymentStatus.APPROVED
    with patch(
        "app.services.finance.ap.supplier_payment.SupplierPaymentService.post_payment",
        return_value=None,
    ):
        processed = PaymentBatchService.process_batch(
            db, org_id, batch.batch_id, processed_by_user_id=uuid4()
        )
    assert processed.status in [APBatchStatus.COMPLETED, APBatchStatus.FAILED]


def test_generate_bank_file_and_get_batch_payments():
    db = MagicMock()
    org_id = uuid4()
    batch = SimpleNamespace(
        batch_id=uuid4(),
        organization_id=org_id,
        status=APBatchStatus.APPROVED,
        batch_number="BATCH-1",
        batch_date=date.today(),
        total_amount=Decimal("100.00"),
        currency_code="NGN",
    )
    payment = SimpleNamespace(
        payment_number="PAY-1",
        supplier_id=uuid4(),
        amount=Decimal("100.00"),
        reference="Ref",
    )
    supplier = SimpleNamespace(trading_name="Supplier", legal_name=None)

    db.scalars.return_value.first.side_effect = [batch, supplier, batch]
    db.scalars.return_value.all.side_effect = [[payment], [payment]]

    with patch("app.services.finance.ap.payment_batch.datetime") as dt:
        dt.now.return_value = datetime(2024, 1, 1, 10, 0, 0)
        dt.strftime = datetime.strftime
        result = PaymentBatchService.generate_bank_file(
            db, org_id, batch.batch_id, file_format="ACH"
        )

    assert result["payment_count"] == 1
    assert "HEADER" in result["content"]
    assert "TRAILER" in result["content"]

    db.scalars.return_value.first.return_value = batch
    db.scalars.return_value.all.return_value = [payment]
    payments = PaymentBatchService.get_batch_payments(db, org_id, batch.batch_id)
    assert payments == [payment]


def test_create_batch_from_invoice_ids_groups_and_links_payments():
    db = MagicMock()
    org_id = uuid4()
    user_id = uuid4()
    supplier_a = uuid4()
    supplier_b = uuid4()
    bank_id = uuid4()
    invoice_a1 = SimpleNamespace(
        invoice_id=uuid4(),
        organization_id=org_id,
        supplier_id=supplier_a,
        invoice_number="INV-A1",
        status=SupplierInvoiceStatus.POSTED,
        currency_code="NGN",
        balance_due=Decimal("25.00"),
    )
    invoice_a2 = SimpleNamespace(
        invoice_id=uuid4(),
        organization_id=org_id,
        supplier_id=supplier_a,
        invoice_number="INV-A2",
        status=SupplierInvoiceStatus.PARTIALLY_PAID,
        currency_code="NGN",
        balance_due=Decimal("75.00"),
    )
    invoice_b1 = SimpleNamespace(
        invoice_id=uuid4(),
        organization_id=org_id,
        supplier_id=supplier_b,
        invoice_number="INV-B1",
        status=SupplierInvoiceStatus.POSTED,
        currency_code="NGN",
        balance_due=Decimal("100.00"),
    )

    db.get.return_value = SimpleNamespace(organization_id=org_id, currency_code="NGN")

    invoice_result = MagicMock()
    invoice_result.all.return_value = [invoice_a1, invoice_a2, invoice_b1]
    inflight_result = MagicMock()
    inflight_result.all.return_value = []
    db.scalars.side_effect = [invoice_result, inflight_result]

    created_payments = []

    def _fake_create_payment(
        db, organization_id, input, created_by_user_id, auto_commit
    ):
        payment = SimpleNamespace(
            payment_id=uuid4(),
            supplier_id=input.supplier_id,
            amount=input.amount,
            payment_batch_id=None,
        )
        created_payments.append(payment)
        return payment

    with (
        patch(
            "app.services.finance.ap.supplier_payment.supplier_payment_service.create_payment",
            side_effect=_fake_create_payment,
        ),
        patch(
            "app.services.finance.ap.payment_batch.SequenceService.get_next_number",
            return_value="001",
        ),
    ):
        batch = PaymentBatchService.create_batch_from_invoice_ids(
            db=db,
            organization_id=org_id,
            batch_date=date.today(),
            payment_method="BANK_TRANSFER",
            bank_account_id=bank_id,
            invoice_ids=[
                invoice_a1.invoice_id,
                invoice_a2.invoice_id,
                invoice_b1.invoice_id,
            ],
            created_by_user_id=user_id,
        )

    assert batch.total_payments == 2
    assert batch.total_amount == Decimal("200.00")
    assert len(created_payments) == 2
    assert {payment.amount for payment in created_payments} == {
        Decimal("100.00"),
        Decimal("100.00"),
    }
    assert all(
        payment.payment_batch_id == batch.batch_id for payment in created_payments
    )
    db.commit.assert_called_once()


def test_create_batch_from_invoice_ids_rejects_non_payable_invoice():
    db = MagicMock()
    org_id = uuid4()
    user_id = uuid4()
    bank_id = uuid4()
    invoice = SimpleNamespace(
        invoice_id=uuid4(),
        organization_id=org_id,
        supplier_id=uuid4(),
        invoice_number="INV-VOID",
        status=SupplierInvoiceStatus.VOID,
        currency_code="NGN",
        balance_due=Decimal("50.00"),
    )

    db.get.return_value = SimpleNamespace(organization_id=org_id, currency_code="NGN")

    invoice_result = MagicMock()
    invoice_result.all.return_value = [invoice]
    inflight_result = MagicMock()
    inflight_result.all.return_value = []
    db.scalars.side_effect = [invoice_result, inflight_result]

    with pytest.raises(HTTPException) as exc:
        PaymentBatchService.create_batch_from_invoice_ids(
            db=db,
            organization_id=org_id,
            batch_date=date.today(),
            payment_method="BANK_TRANSFER",
            bank_account_id=bank_id,
            invoice_ids=[invoice.invoice_id],
            created_by_user_id=user_id,
        )

    assert exc.value.status_code == 400
    assert "not payable" in exc.value.detail
