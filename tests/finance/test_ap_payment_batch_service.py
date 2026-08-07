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
from app.models.finance.tax.tax_code import TaxType
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
    batch_id = uuid4()
    supplier_id = uuid4()
    batch = SimpleNamespace(
        batch_id=batch_id,
        organization_id=org_id,
        status=APBatchStatus.APPROVED,
        batch_number="BATCH-1",
        batch_date=date.today(),
        total_amount=Decimal("100.00"),
        currency_code="NGN",
        bank_account_id=uuid4(),
        bank_file_generated=False,
        bank_file_reference=None,
        bank_file_generated_at=None,
    )
    payment = SimpleNamespace(
        payment_number="PAY-1",
        supplier_id=supplier_id,
        amount=Decimal("100.00"),
        reference="Ref",
    )
    supplier = SimpleNamespace(
        supplier_id=supplier_id,
        trading_name="Supplier",
        legal_name=None,
        supplier_code="SUP-001",
        bank_details={
            "account_number": "0123456789",
            "bank_name": "Zenith Bank",
            "account_name": "Supplier Ltd",
        },
    )
    bank_account = SimpleNamespace(account_number="1011649523")

    db.scalars.return_value.first.side_effect = [batch]
    db.scalars.return_value.all.side_effect = [[payment]]
    db.get.side_effect = [bank_account, supplier]

    upload_result = SimpleNamespace(
        content=b"excel-content",
        filename="bank_upload.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        row_count=1,
        total_amount=Decimal("100.00"),
        errors=[],
    )

    with (
        patch("app.services.finance.ap.payment_batch.datetime") as dt,
        patch(
            "app.services.finance.banking.bank_upload.BankUploadService.generate_upload",
            return_value=upload_result,
        ),
    ):
        dt.now.return_value = datetime(2024, 1, 1, 10, 0, 0)
        dt.strftime = datetime.strftime
        result = PaymentBatchService.generate_bank_file(
            db, org_id, batch.batch_id, bank_format="zenith"
        )

    assert result["payment_count"] == 1
    assert result["content"] == b"excel-content"
    assert result["filename"] == "bank_upload.xlsx"

    db.scalars.return_value.first.side_effect = None
    db.scalars.return_value.first.return_value = batch
    db.scalars.return_value.all.side_effect = None
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
    db.flush.assert_called()


def test_create_batch_from_invoice_ids_inherits_planned_wht():
    db = MagicMock()
    org_id = uuid4()
    user_id = uuid4()
    supplier_id = uuid4()
    bank_id = uuid4()
    wht_code_id = uuid4()
    invoice = SimpleNamespace(
        invoice_id=uuid4(),
        organization_id=org_id,
        supplier_id=supplier_id,
        invoice_number="INV-WHT",
        status=SupplierInvoiceStatus.POSTED,
        currency_code="NGN",
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
        if record_id == bank_id:
            return SimpleNamespace(organization_id=org_id, currency_code="NGN")
        if model.__name__ == "TaxCode" and record_id == wht_code_id:
            return wht_code
        return None

    db.get.side_effect = get_record
    invoice_result = MagicMock()
    invoice_result.all.return_value = [invoice]
    inflight_result = MagicMock()
    inflight_result.all.return_value = []
    db.scalars.side_effect = [invoice_result, inflight_result]

    captured_inputs = []

    def _fake_create_payment(
        db, organization_id, input, created_by_user_id, auto_commit
    ):
        captured_inputs.append(input)
        return SimpleNamespace(
            payment_id=uuid4(),
            supplier_id=input.supplier_id,
            amount=input.amount,
            payment_batch_id=None,
        )

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
            invoice_ids=[invoice.invoice_id],
            created_by_user_id=user_id,
        )

    payment_input = captured_inputs[0]
    assert payment_input.gross_amount == Decimal("1075.00")
    assert payment_input.amount == Decimal("1025.00")
    assert payment_input.wht_amount == Decimal("50.00")
    assert payment_input.allocations[0].amount == Decimal("1075.00")
    assert batch.total_amount == Decimal("1025.00")


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
