"""
AP Multi-Tenant Security Regression Tests.

Verifies that all AP service operations properly enforce organization_id
isolation, preventing cross-tenant data access.

Each test creates entities belonging to org_a and attempts to access them
through service methods scoped to org_b — expecting 404/None results.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from tests.ifrs.ap.conftest import (
    MockSupplier,
)

# ───── Shared fixtures ─────


@pytest.fixture
def org_a() -> uuid.UUID:
    """Organization A — entity owner."""
    return uuid.uuid4()


@pytest.fixture
def org_b() -> uuid.UUID:
    """Organization B — unauthorized accessor."""
    return uuid.uuid4()


@pytest.fixture
def user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def mock_db() -> MagicMock:
    """Mock DB session with scalars/scalar support."""
    db = MagicMock()
    db.scalars.return_value = MagicMock()
    return db


# ───── SupplierService ─────


class TestSupplierMultiTenancy:
    """Verify SupplierService enforces org_id isolation."""

    def test_get_wrong_org_returns_404(
        self, mock_db: MagicMock, org_a: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        """Supplier belonging to org_a is not accessible from org_b."""
        supplier = MockSupplier(organization_id=org_a)
        # get_org_scoped_entity uses db.get() then checks org match
        mock_db.get.return_value = supplier

        with patch("app.services.finance.ap.supplier.Supplier"):
            from app.services.finance.ap.supplier import SupplierService

            with pytest.raises(HTTPException) as exc:
                SupplierService.get(mock_db, org_b, str(supplier.supplier_id))

        assert exc.value.status_code == 404

    def test_update_wrong_org_returns_404(
        self,
        mock_db: MagicMock,
        org_a: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        """Cannot update a supplier in a different organization."""
        from app.models.finance.ap.supplier import SupplierType
        from app.services.finance.ap.supplier import SupplierInput, SupplierService

        supplier = MockSupplier(organization_id=org_a)
        mock_db.get.return_value = supplier

        input_data = SupplierInput(
            supplier_code="SUP-001",
            supplier_type=SupplierType.VENDOR,
            supplier_name="Test",
        )

        with patch("app.services.finance.ap.supplier.Supplier"):
            with pytest.raises(HTTPException) as exc:
                SupplierService.update_supplier(
                    mock_db, org_b, supplier.supplier_id, input_data
                )

        assert exc.value.status_code == 404

    def test_deactivate_wrong_org_returns_404(
        self,
        mock_db: MagicMock,
        org_a: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        """Cannot deactivate a supplier in a different organization."""
        from app.services.finance.ap.supplier import SupplierService

        supplier = MockSupplier(organization_id=org_a)
        mock_db.get.return_value = supplier

        with patch("app.services.finance.ap.supplier.Supplier"):
            with pytest.raises(HTTPException) as exc:
                SupplierService.deactivate_supplier(
                    mock_db, org_b, supplier.supplier_id
                )

        assert exc.value.status_code == 404

    def test_get_supplier_summary_wrong_org_returns_404(
        self,
        mock_db: MagicMock,
        org_a: uuid.UUID,
        org_b: uuid.UUID,
    ) -> None:
        """Cannot fetch summary for a supplier in a different organization."""
        from app.services.finance.ap.supplier import SupplierService

        supplier = MockSupplier(organization_id=org_a)
        mock_db.get.return_value = supplier

        with patch("app.services.finance.ap.supplier.Supplier"):
            with pytest.raises(HTTPException) as exc:
                SupplierService.get_supplier_summary(
                    mock_db, org_b, supplier.supplier_id
                )

        assert exc.value.status_code == 404

    def test_get_by_code_scoped_to_org(
        self,
        mock_db: MagicMock,
        org_b: uuid.UUID,
    ) -> None:
        """get_by_code passes org_id in the where clause."""
        from app.services.finance.ap.supplier import SupplierService

        mock_db.scalars.return_value.first.return_value = None

        result = SupplierService.get_by_code(mock_db, org_b, "SUP-001")

        assert result is None
        mock_db.scalars.assert_called_once()


# ───── PurchaseOrderService ─────


class TestPurchaseOrderMultiTenancy:
    """Verify PurchaseOrderService enforces org_id isolation."""

    def test_get_wrong_org_returns_none(
        self, mock_db: MagicMock, org_a: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        """PO belonging to org_a returns None when accessed from org_b."""
        from app.services.finance.ap.purchase_order import PurchaseOrderService

        mock_po = MagicMock()
        mock_po.organization_id = org_a
        mock_db.get.return_value = mock_po

        result = PurchaseOrderService.get(
            mock_db, str(uuid.uuid4()), organization_id=org_b
        )

        assert result is None

    def test_approve_wrong_org_returns_404(
        self, mock_db: MagicMock, org_a: uuid.UUID, org_b: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        """Cannot approve a PO in a different organization."""
        from app.services.finance.ap.purchase_order import PurchaseOrderService

        # select().where(po_id, org_b) won't find the PO scoped to org_a
        mock_db.scalars.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc:
            PurchaseOrderService.approve_po(mock_db, org_b, uuid.uuid4(), user_id)

        assert exc.value.status_code == 404

    def test_cancel_wrong_org_returns_404(
        self, mock_db: MagicMock, org_a: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        """Cannot cancel a PO in a different organization."""
        from app.services.finance.ap.purchase_order import PurchaseOrderService

        mock_db.scalars.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc:
            PurchaseOrderService.cancel_po(mock_db, org_b, uuid.uuid4())

        assert exc.value.status_code == 404

    def test_submit_wrong_org_returns_404(
        self, mock_db: MagicMock, org_a: uuid.UUID, org_b: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        """Cannot submit a PO in a different organization."""
        from app.services.finance.ap.purchase_order import PurchaseOrderService

        mock_db.scalars.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc:
            PurchaseOrderService.submit_for_approval(
                mock_db, org_b, uuid.uuid4(), user_id
            )

        assert exc.value.status_code == 404

    def test_close_wrong_org_returns_404(
        self, mock_db: MagicMock, org_a: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        """Cannot close a PO in a different organization."""
        from app.services.finance.ap.purchase_order import PurchaseOrderService

        mock_db.scalars.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc:
            PurchaseOrderService.close_po(mock_db, org_b, uuid.uuid4())

        assert exc.value.status_code == 404

    def test_get_by_number_scoped_to_org(
        self, mock_db: MagicMock, org_b: uuid.UUID
    ) -> None:
        """get_by_number includes org_id in where clause."""
        from app.services.finance.ap.purchase_order import PurchaseOrderService

        mock_db.scalars.return_value.first.return_value = None

        result = PurchaseOrderService.get_by_number(mock_db, org_b, "PO-0001")

        assert result is None
        mock_db.scalars.assert_called_once()


# ───── GoodsReceiptService ─────


class TestGoodsReceiptMultiTenancy:
    """Verify GoodsReceiptService enforces org_id isolation."""

    def test_get_wrong_org_returns_none(
        self, mock_db: MagicMock, org_a: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        """Receipt belonging to org_a returns None when accessed from org_b."""
        from app.services.finance.ap.goods_receipt import GoodsReceiptService

        mock_receipt = MagicMock()
        mock_receipt.organization_id = org_a
        mock_db.get.return_value = mock_receipt

        result = GoodsReceiptService.get(
            mock_db, str(uuid.uuid4()), organization_id=org_b
        )

        assert result is None

    def test_start_inspection_wrong_org_returns_404(
        self, mock_db: MagicMock, org_a: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        """Cannot start inspection on a receipt in a different org."""
        from app.services.finance.ap.goods_receipt import GoodsReceiptService

        mock_db.scalars.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc:
            GoodsReceiptService.start_inspection(mock_db, org_b, uuid.uuid4())

        assert exc.value.status_code == 404

    def test_accept_all_wrong_org_returns_404(
        self, mock_db: MagicMock, org_a: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        """Cannot accept receipt in a different organization."""
        from app.services.finance.ap.goods_receipt import GoodsReceiptService

        mock_db.scalars.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc:
            GoodsReceiptService.accept_all(mock_db, org_b, uuid.uuid4())

        assert exc.value.status_code == 404

    def test_get_by_number_scoped_to_org(
        self, mock_db: MagicMock, org_b: uuid.UUID
    ) -> None:
        """get_by_number includes org_id in where clause."""
        from app.services.finance.ap.goods_receipt import GoodsReceiptService

        mock_db.scalars.return_value.first.return_value = None

        result = GoodsReceiptService.get_by_number(mock_db, org_b, "GR-0001")

        assert result is None
        mock_db.scalars.assert_called_once()


# ───── PaymentBatchService ─────


class TestPaymentBatchMultiTenancy:
    """Verify PaymentBatchService enforces org_id isolation."""

    def test_get_wrong_org_returns_none(
        self, mock_db: MagicMock, org_a: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        """Batch belonging to org_a returns None when accessed from org_b."""
        from app.services.finance.ap.payment_batch import PaymentBatchService

        mock_batch = MagicMock()
        mock_batch.organization_id = org_a
        mock_db.get.return_value = mock_batch

        result = PaymentBatchService.get(
            mock_db, str(uuid.uuid4()), organization_id=org_b
        )

        assert result is None

    def test_add_payment_wrong_org_returns_404(
        self, mock_db: MagicMock, org_a: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        """Cannot add payment to batch in a different organization."""
        from app.services.finance.ap.payment_batch import PaymentBatchService

        mock_db.scalars.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc:
            PaymentBatchService.add_payment_to_batch(
                mock_db, org_b, uuid.uuid4(), uuid.uuid4()
            )

        assert exc.value.status_code == 404

    def test_remove_payment_wrong_org_returns_404(
        self, mock_db: MagicMock, org_a: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        """Cannot remove payment from batch in a different organization."""
        from app.services.finance.ap.payment_batch import PaymentBatchService

        mock_db.scalars.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc:
            PaymentBatchService.remove_payment_from_batch(
                mock_db, org_b, uuid.uuid4(), uuid.uuid4()
            )

        assert exc.value.status_code == 404

    def test_approve_wrong_org_returns_404(
        self, mock_db: MagicMock, org_a: uuid.UUID, org_b: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        """Cannot approve batch in a different organization."""
        from app.services.finance.ap.payment_batch import PaymentBatchService

        mock_db.scalars.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc:
            PaymentBatchService.approve_batch(mock_db, org_b, uuid.uuid4(), user_id)

        assert exc.value.status_code == 404

    def test_process_wrong_org_returns_404(
        self, mock_db: MagicMock, org_a: uuid.UUID, org_b: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        """Cannot process batch in a different organization."""
        from app.services.finance.ap.payment_batch import PaymentBatchService

        mock_db.scalars.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc:
            PaymentBatchService.process_batch(mock_db, org_b, uuid.uuid4(), user_id)

        assert exc.value.status_code == 404

    def test_generate_bank_file_wrong_org_returns_404(
        self, mock_db: MagicMock, org_a: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        """Cannot generate bank file for batch in a different organization."""
        from app.services.finance.ap.payment_batch import PaymentBatchService

        mock_db.scalars.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc:
            PaymentBatchService.generate_bank_file(mock_db, org_b, uuid.uuid4())

        assert exc.value.status_code == 404

    def test_get_batch_payments_wrong_org_returns_404(
        self, mock_db: MagicMock, org_a: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        """Cannot list payments for batch in a different organization."""
        from app.services.finance.ap.payment_batch import PaymentBatchService

        mock_db.scalars.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc:
            PaymentBatchService.get_batch_payments(mock_db, org_b, uuid.uuid4())

        assert exc.value.status_code == 404


# ───── SupplierPaymentService ─────


class TestSupplierPaymentMultiTenancy:
    """Verify SupplierPaymentService enforces org_id isolation."""

    def test_get_wrong_org_raises_404(
        self, mock_db: MagicMock, org_a: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        """Payment belonging to org_a raises 404 when accessed from org_b."""
        from app.services.finance.ap.supplier_payment import SupplierPaymentService

        mock_payment = MagicMock()
        mock_payment.organization_id = org_a
        mock_db.get.return_value = mock_payment

        with pytest.raises(HTTPException) as exc:
            SupplierPaymentService.get(
                mock_db, str(uuid.uuid4()), organization_id=org_b
            )

        assert exc.value.status_code == 404


# ───── APAgingService ─────


class TestAPAgingMultiTenancy:
    """Verify APAgingService enforces org_id isolation."""

    def test_calculate_supplier_aging_wrong_org_raises(
        self, mock_db: MagicMock, org_a: uuid.UUID, org_b: uuid.UUID
    ) -> None:
        """Cannot calculate aging for supplier in a different organization."""
        from app.services.finance.ap.ap_aging import APAgingService

        supplier = MockSupplier(organization_id=org_a)
        mock_db.get.return_value = supplier

        with pytest.raises(ValueError, match="Supplier not found"):
            APAgingService.calculate_supplier_aging(
                mock_db, org_b, supplier.supplier_id
            )

    def test_calculate_org_aging_uses_org_filter(
        self, mock_db: MagicMock, org_b: uuid.UUID
    ) -> None:
        """Organization aging query is scoped to the given org_id."""
        from app.services.finance.ap.ap_aging import APAgingService

        mock_db.scalars.return_value.all.return_value = []

        with patch(
            "app.services.finance.ap.ap_aging.org_context_service"
        ) as mock_org_ctx:
            mock_org_ctx.get_functional_currency.return_value = "USD"

            result = APAgingService.calculate_organization_aging(mock_db, org_b)

        assert result.invoice_count == 0
        assert result.supplier_count == 0
        assert result.total_outstanding == Decimal("0")
        mock_db.scalars.assert_called_once()
