"""
Unit tests for GoodsReceiptService.

Tests cover:
- Goods receipt creation against PO
- Inspection workflow (start, complete, accept all)
- Acceptance/rejection handling
- PO quantity and status updates
- Getter and list methods
"""

import pytest
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch, PropertyMock
from uuid import uuid4

from fastapi import HTTPException

from app.services.finance.ap.goods_receipt import (
    GoodsReceiptService,
    GoodsReceiptInput,
    GRLineInput,
    InspectionResult,
)


# Mock enums
class MockReceiptStatus:
    RECEIVED = "RECEIVED"
    INSPECTING = "INSPECTING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    PARTIAL = "PARTIAL"

    @property
    def value(self):
        return self


class MockPOStatus:
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"
    RECEIVED = "RECEIVED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"

    @property
    def value(self):
        return self


class MockPurchaseOrderLine:
    """Mock PurchaseOrderLine model."""

    def __init__(
        self,
        line_id=None,
        po_id=None,
        line_number=1,
        item_id=None,
        description="Test Item",
        quantity_ordered=Decimal("10.00"),
        quantity_received=Decimal("0"),
        unit_price=Decimal("100.00"),
        line_amount=Decimal("1000.00"),
    ):
        self.line_id = line_id or uuid4()
        self.po_id = po_id or uuid4()
        self.line_number = line_number
        self.item_id = item_id
        self.description = description
        self.quantity_ordered = quantity_ordered
        self.quantity_received = quantity_received
        self.unit_price = unit_price
        self.line_amount = line_amount


class MockPurchaseOrder:
    """Mock PurchaseOrder model."""

    def __init__(
        self,
        po_id=None,
        organization_id=None,
        supplier_id=None,
        po_number="PO-000001",
        status=None,
        lines=None,
        amount_received=Decimal("0"),
    ):
        self.po_id = po_id or uuid4()
        self.organization_id = organization_id or uuid4()
        self.supplier_id = supplier_id or uuid4()
        self.po_number = po_number
        self.status = status or MockPOStatus()
        self._lines = lines or []
        self.amount_received = amount_received

    @property
    def lines(self):
        return self._lines


class MockGoodsReceipt:
    """Mock GoodsReceipt model."""

    def __init__(
        self,
        receipt_id=None,
        organization_id=None,
        supplier_id=None,
        po_id=None,
        receipt_number="GR-000001",
        receipt_date=None,
        status=None,
        received_by_user_id=None,
        warehouse_id=None,
        notes=None,
        lines=None,
    ):
        self.receipt_id = receipt_id or uuid4()
        self.organization_id = organization_id or uuid4()
        self.supplier_id = supplier_id or uuid4()
        self.po_id = po_id or uuid4()
        self.receipt_number = receipt_number
        self.receipt_date = receipt_date or date.today()
        self.status = status or MockReceiptStatus()
        self.received_by_user_id = received_by_user_id or uuid4()
        self.warehouse_id = warehouse_id
        self.notes = notes
        self._lines = lines or []

    @property
    def lines(self):
        return self._lines


class MockGoodsReceiptLine:
    """Mock GoodsReceiptLine model."""

    def __init__(
        self,
        line_id=None,
        receipt_id=None,
        po_line_id=None,
        line_number=1,
        quantity_received=Decimal("10.00"),
        quantity_accepted=Decimal("0"),
        quantity_rejected=Decimal("0"),
        location_id=None,
        lot_number=None,
        serial_numbers=None,
        rejection_reason=None,
    ):
        self.line_id = line_id or uuid4()
        self.receipt_id = receipt_id or uuid4()
        self.po_line_id = po_line_id or uuid4()
        self.line_number = line_number
        self.quantity_received = quantity_received
        self.quantity_accepted = quantity_accepted
        self.quantity_rejected = quantity_rejected
        self.location_id = location_id
        self.lot_number = lot_number
        self.serial_numbers = serial_numbers
        self.rejection_reason = rejection_reason


# ===================== CREATE RECEIPT TESTS =====================

class TestCreateReceipt:
    """Tests for goods receipt creation."""

    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceiptService._update_po_status")
    @patch("app.services.ifrs.ap.goods_receipt.SequenceService")
    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceiptLine")
    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceipt")
    @patch("app.services.ifrs.ap.goods_receipt.PurchaseOrderLine")
    @patch("app.services.ifrs.ap.goods_receipt.POStatus")
    @patch("app.services.ifrs.ap.goods_receipt.PurchaseOrder")
    def test_create_receipt_success(
        self,
        mock_po_class,
        mock_po_status,
        mock_po_line_class,
        mock_receipt_class,
        mock_line_class,
        mock_seq_service,
        mock_update_status,
    ):
        """Test successful goods receipt creation."""
        db = MagicMock()
        org_id = uuid4()
        po_id = uuid4()
        user_id = uuid4()
        po_line_id = uuid4()

        # Setup status mocks
        mock_approved = MagicMock()
        mock_partial = MagicMock()
        mock_po_status.APPROVED = mock_approved
        mock_po_status.PARTIALLY_RECEIVED = mock_partial

        # Create mock PO with lines
        mock_po_line = MockPurchaseOrderLine(
            line_id=po_line_id,
            quantity_ordered=Decimal("10"),
            quantity_received=Decimal("0"),
        )
        mock_po = MockPurchaseOrder(
            po_id=po_id,
            organization_id=org_id,
            lines=[mock_po_line],
        )
        mock_po.status = mock_approved

        db.query.return_value.filter.return_value.first.side_effect = [
            mock_po,  # PO lookup
            mock_po_line,  # PO line lookup
        ]

        mock_seq_service.get_next_number.return_value = "GR-000001"

        mock_receipt = MockGoodsReceipt(
            organization_id=org_id,
            po_id=po_id,
        )
        mock_receipt_class.return_value = mock_receipt

        input_data = GoodsReceiptInput(
            po_id=po_id,
            receipt_date=date.today(),
            lines=[
                GRLineInput(
                    po_line_id=po_line_id,
                    quantity_received=Decimal("5"),
                )
            ],
        )

        result = GoodsReceiptService.create_receipt(db, org_id, input_data, user_id)

        assert result is not None
        db.add.assert_called()
        db.commit.assert_called_once()
        mock_seq_service.get_next_number.assert_called_once()

    @patch("app.services.ifrs.ap.goods_receipt.PurchaseOrder")
    def test_create_receipt_po_not_found(self, mock_po_class):
        """Test receipt creation with non-existent PO."""
        db = MagicMock()
        org_id = uuid4()
        user_id = uuid4()

        db.query.return_value.filter.return_value.first.return_value = None

        input_data = GoodsReceiptInput(
            po_id=uuid4(),
            receipt_date=date.today(),
            lines=[
                GRLineInput(
                    po_line_id=uuid4(),
                    quantity_received=Decimal("5"),
                )
            ],
        )

        with pytest.raises(HTTPException) as exc_info:
            GoodsReceiptService.create_receipt(db, org_id, input_data, user_id)

        assert exc_info.value.status_code == 404
        assert "Purchase order not found" in str(exc_info.value.detail)

    @patch("app.services.ifrs.ap.goods_receipt.POStatus")
    @patch("app.services.ifrs.ap.goods_receipt.PurchaseOrder")
    def test_create_receipt_po_wrong_status(self, mock_po_class, mock_po_status):
        """Test receipt creation for PO not in receivable status."""
        db = MagicMock()
        org_id = uuid4()
        po_id = uuid4()
        user_id = uuid4()

        mock_approved = MagicMock()
        mock_partial = MagicMock()
        mock_draft = MagicMock()
        mock_draft.value = "DRAFT"
        mock_po_status.APPROVED = mock_approved
        mock_po_status.PARTIALLY_RECEIVED = mock_partial

        mock_po = MockPurchaseOrder(po_id=po_id, organization_id=org_id)
        mock_po.status = mock_draft

        db.query.return_value.filter.return_value.first.return_value = mock_po

        input_data = GoodsReceiptInput(
            po_id=po_id,
            receipt_date=date.today(),
            lines=[
                GRLineInput(
                    po_line_id=uuid4(),
                    quantity_received=Decimal("5"),
                )
            ],
        )

        with pytest.raises(HTTPException) as exc_info:
            GoodsReceiptService.create_receipt(db, org_id, input_data, user_id)

        assert exc_info.value.status_code == 400
        assert "Cannot receive goods" in str(exc_info.value.detail)

    @patch("app.services.ifrs.ap.goods_receipt.POStatus")
    @patch("app.services.ifrs.ap.goods_receipt.PurchaseOrder")
    def test_create_receipt_no_lines(self, mock_po_class, mock_po_status):
        """Test receipt creation without lines fails."""
        db = MagicMock()
        org_id = uuid4()
        po_id = uuid4()
        user_id = uuid4()

        mock_approved = MagicMock()
        mock_partial = MagicMock()
        mock_po_status.APPROVED = mock_approved
        mock_po_status.PARTIALLY_RECEIVED = mock_partial

        mock_po = MockPurchaseOrder(po_id=po_id, organization_id=org_id)
        mock_po.status = mock_approved

        db.query.return_value.filter.return_value.first.return_value = mock_po

        input_data = GoodsReceiptInput(
            po_id=po_id,
            receipt_date=date.today(),
            lines=[],  # Empty lines
        )

        with pytest.raises(HTTPException) as exc_info:
            GoodsReceiptService.create_receipt(db, org_id, input_data, user_id)

        assert exc_info.value.status_code == 400
        assert "at least one line" in str(exc_info.value.detail).lower()

    @patch("app.services.ifrs.ap.goods_receipt.POStatus")
    @patch("app.services.ifrs.ap.goods_receipt.PurchaseOrder")
    def test_create_receipt_invalid_po_line(self, mock_po_class, mock_po_status):
        """Test receipt creation with invalid PO line."""
        db = MagicMock()
        org_id = uuid4()
        po_id = uuid4()
        user_id = uuid4()
        valid_po_line_id = uuid4()
        invalid_po_line_id = uuid4()

        mock_approved = MagicMock()
        mock_partial = MagicMock()
        mock_po_status.APPROVED = mock_approved
        mock_po_status.PARTIALLY_RECEIVED = mock_partial

        # PO has one line
        mock_po_line = MockPurchaseOrderLine(line_id=valid_po_line_id)
        mock_po = MockPurchaseOrder(
            po_id=po_id,
            organization_id=org_id,
            lines=[mock_po_line],
        )
        mock_po.status = mock_approved

        db.query.return_value.filter.return_value.first.return_value = mock_po

        input_data = GoodsReceiptInput(
            po_id=po_id,
            receipt_date=date.today(),
            lines=[
                GRLineInput(
                    po_line_id=invalid_po_line_id,  # Not in PO
                    quantity_received=Decimal("5"),
                )
            ],
        )

        with pytest.raises(HTTPException) as exc_info:
            GoodsReceiptService.create_receipt(db, org_id, input_data, user_id)

        assert exc_info.value.status_code == 400
        assert "not found in this purchase order" in str(exc_info.value.detail)

    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceiptService._update_po_status")
    @patch("app.services.ifrs.ap.goods_receipt.SequenceService")
    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceiptLine")
    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceipt")
    @patch("app.services.ifrs.ap.goods_receipt.PurchaseOrderLine")
    @patch("app.services.ifrs.ap.goods_receipt.POStatus")
    @patch("app.services.ifrs.ap.goods_receipt.PurchaseOrder")
    def test_create_receipt_quantity_exceeds_remaining(
        self,
        mock_po_class,
        mock_po_status,
        mock_po_line_class,
        mock_receipt_class,
        mock_line_class,
        mock_seq_service,
        mock_update_status,
    ):
        """Test receipt creation fails when quantity exceeds remaining."""
        db = MagicMock()
        org_id = uuid4()
        po_id = uuid4()
        user_id = uuid4()
        po_line_id = uuid4()

        mock_approved = MagicMock()
        mock_partial = MagicMock()
        mock_po_status.APPROVED = mock_approved
        mock_po_status.PARTIALLY_RECEIVED = mock_partial

        # PO line with partial receipt already
        mock_po_line = MockPurchaseOrderLine(
            line_id=po_line_id,
            quantity_ordered=Decimal("10"),
            quantity_received=Decimal("8"),  # Only 2 remaining
        )
        mock_po = MockPurchaseOrder(
            po_id=po_id,
            organization_id=org_id,
            lines=[mock_po_line],
        )
        mock_po.status = mock_approved

        db.query.return_value.filter.return_value.first.side_effect = [
            mock_po,  # PO lookup
            mock_po_line,  # PO line lookup
        ]

        mock_seq_service.get_next_number.return_value = "GR-000001"

        mock_receipt = MockGoodsReceipt(organization_id=org_id, po_id=po_id)
        mock_receipt_class.return_value = mock_receipt

        input_data = GoodsReceiptInput(
            po_id=po_id,
            receipt_date=date.today(),
            lines=[
                GRLineInput(
                    po_line_id=po_line_id,
                    quantity_received=Decimal("5"),  # Exceeds remaining 2
                )
            ],
        )

        with pytest.raises(HTTPException) as exc_info:
            GoodsReceiptService.create_receipt(db, org_id, input_data, user_id)

        assert exc_info.value.status_code == 400
        assert "exceeds remaining" in str(exc_info.value.detail).lower()


# ===================== START INSPECTION TESTS =====================

class TestStartInspection:
    """Tests for starting inspection."""

    @patch("app.services.ifrs.ap.goods_receipt.ReceiptStatus")
    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceipt")
    def test_start_inspection_success(self, mock_receipt_class, mock_status_class):
        """Test successful inspection start."""
        db = MagicMock()
        org_id = uuid4()
        receipt_id = uuid4()

        mock_received = MagicMock()
        mock_received.value = "RECEIVED"
        mock_inspecting = MagicMock()
        mock_status_class.RECEIVED = mock_received
        mock_status_class.INSPECTING = mock_inspecting

        mock_receipt = MockGoodsReceipt(
            receipt_id=receipt_id,
            organization_id=org_id,
        )
        mock_receipt.status = mock_received

        db.query.return_value.filter.return_value.first.return_value = mock_receipt

        result = GoodsReceiptService.start_inspection(db, org_id, receipt_id)

        assert result is not None
        assert mock_receipt.status == mock_inspecting
        db.commit.assert_called_once()

    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceipt")
    def test_start_inspection_not_found(self, mock_receipt_class):
        """Test starting inspection on non-existent receipt."""
        db = MagicMock()
        org_id = uuid4()

        db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            GoodsReceiptService.start_inspection(db, org_id, uuid4())

        assert exc_info.value.status_code == 404

    @patch("app.services.ifrs.ap.goods_receipt.ReceiptStatus")
    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceipt")
    def test_start_inspection_wrong_status(self, mock_receipt_class, mock_status_class):
        """Test starting inspection on receipt not in RECEIVED status."""
        db = MagicMock()
        org_id = uuid4()
        receipt_id = uuid4()

        mock_received = MagicMock()
        mock_received.value = "RECEIVED"
        mock_accepted = MagicMock()
        mock_accepted.value = "ACCEPTED"
        mock_status_class.RECEIVED = mock_received

        mock_receipt = MockGoodsReceipt(receipt_id=receipt_id, organization_id=org_id)
        mock_receipt.status = mock_accepted

        db.query.return_value.filter.return_value.first.return_value = mock_receipt

        with pytest.raises(HTTPException) as exc_info:
            GoodsReceiptService.start_inspection(db, org_id, receipt_id)

        assert exc_info.value.status_code == 400
        assert "Cannot start inspection" in str(exc_info.value.detail)


# ===================== COMPLETE INSPECTION TESTS =====================

class TestCompleteInspection:
    """Tests for completing inspection."""

    @patch("app.services.ifrs.ap.goods_receipt.ReceiptStatus")
    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceiptLine")
    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceipt")
    def test_complete_inspection_all_accepted(
        self, mock_receipt_class, mock_line_class, mock_status_class
    ):
        """Test inspection completion with all items accepted."""
        db = MagicMock()
        org_id = uuid4()
        receipt_id = uuid4()
        line_id = uuid4()

        mock_received = MagicMock()
        mock_inspecting = MagicMock()
        mock_accepted = MagicMock()
        mock_rejected = MagicMock()
        mock_partial = MagicMock()
        mock_status_class.RECEIVED = mock_received
        mock_status_class.INSPECTING = mock_inspecting
        mock_status_class.ACCEPTED = mock_accepted
        mock_status_class.REJECTED = mock_rejected
        mock_status_class.PARTIAL = mock_partial

        mock_receipt = MockGoodsReceipt(receipt_id=receipt_id, organization_id=org_id)
        mock_receipt.status = mock_inspecting

        mock_line = MockGoodsReceiptLine(
            line_id=line_id,
            receipt_id=receipt_id,
            quantity_received=Decimal("10"),
        )

        db.query.return_value.filter.return_value.first.side_effect = [
            mock_receipt,  # Receipt lookup
            mock_line,  # Line lookup
        ]

        inspection_results = [
            InspectionResult(
                line_id=line_id,
                quantity_accepted=Decimal("10"),
                quantity_rejected=Decimal("0"),
            )
        ]

        result = GoodsReceiptService.complete_inspection(
            db, org_id, receipt_id, inspection_results
        )

        assert result is not None
        assert mock_receipt.status == mock_accepted
        db.commit.assert_called_once()

    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceiptService._reverse_po_quantities")
    @patch("app.services.ifrs.ap.goods_receipt.ReceiptStatus")
    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceiptLine")
    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceipt")
    def test_complete_inspection_all_rejected(
        self, mock_receipt_class, mock_line_class, mock_status_class, mock_reverse
    ):
        """Test inspection completion with all items rejected."""
        db = MagicMock()
        org_id = uuid4()
        receipt_id = uuid4()
        line_id = uuid4()

        mock_received = MagicMock()
        mock_inspecting = MagicMock()
        mock_accepted = MagicMock()
        mock_rejected = MagicMock()
        mock_partial = MagicMock()
        mock_status_class.RECEIVED = mock_received
        mock_status_class.INSPECTING = mock_inspecting
        mock_status_class.ACCEPTED = mock_accepted
        mock_status_class.REJECTED = mock_rejected
        mock_status_class.PARTIAL = mock_partial

        mock_receipt = MockGoodsReceipt(receipt_id=receipt_id, organization_id=org_id)
        mock_receipt.status = mock_inspecting

        mock_line = MockGoodsReceiptLine(
            line_id=line_id,
            receipt_id=receipt_id,
            quantity_received=Decimal("10"),
        )

        db.query.return_value.filter.return_value.first.side_effect = [
            mock_receipt,
            mock_line,
        ]

        inspection_results = [
            InspectionResult(
                line_id=line_id,
                quantity_accepted=Decimal("0"),
                quantity_rejected=Decimal("10"),
                rejection_reason="Damaged in transit",
            )
        ]

        result = GoodsReceiptService.complete_inspection(
            db, org_id, receipt_id, inspection_results
        )

        assert result is not None
        assert mock_receipt.status == mock_rejected
        mock_reverse.assert_called_once()  # PO quantities should be reversed
        db.commit.assert_called_once()

    @patch("app.services.ifrs.ap.goods_receipt.ReceiptStatus")
    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceiptLine")
    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceipt")
    def test_complete_inspection_partial(
        self, mock_receipt_class, mock_line_class, mock_status_class
    ):
        """Test inspection completion with partial acceptance."""
        db = MagicMock()
        org_id = uuid4()
        receipt_id = uuid4()
        line_id = uuid4()

        mock_received = MagicMock()
        mock_inspecting = MagicMock()
        mock_accepted = MagicMock()
        mock_rejected = MagicMock()
        mock_partial = MagicMock()
        mock_status_class.RECEIVED = mock_received
        mock_status_class.INSPECTING = mock_inspecting
        mock_status_class.ACCEPTED = mock_accepted
        mock_status_class.REJECTED = mock_rejected
        mock_status_class.PARTIAL = mock_partial

        mock_receipt = MockGoodsReceipt(receipt_id=receipt_id, organization_id=org_id)
        mock_receipt.status = mock_inspecting

        mock_line = MockGoodsReceiptLine(
            line_id=line_id,
            receipt_id=receipt_id,
            quantity_received=Decimal("10"),
        )

        db.query.return_value.filter.return_value.first.side_effect = [
            mock_receipt,
            mock_line,
        ]

        inspection_results = [
            InspectionResult(
                line_id=line_id,
                quantity_accepted=Decimal("7"),
                quantity_rejected=Decimal("3"),
                rejection_reason="Quality issue",
            )
        ]

        result = GoodsReceiptService.complete_inspection(
            db, org_id, receipt_id, inspection_results
        )

        assert result is not None
        assert mock_receipt.status == mock_partial

    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceipt")
    def test_complete_inspection_not_found(self, mock_receipt_class):
        """Test completing inspection on non-existent receipt."""
        db = MagicMock()
        org_id = uuid4()

        db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            GoodsReceiptService.complete_inspection(db, org_id, uuid4(), [])

        assert exc_info.value.status_code == 404

    @patch("app.services.ifrs.ap.goods_receipt.ReceiptStatus")
    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceiptLine")
    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceipt")
    def test_complete_inspection_quantity_mismatch(
        self, mock_receipt_class, mock_line_class, mock_status_class
    ):
        """Test inspection fails when quantities don't add up."""
        db = MagicMock()
        org_id = uuid4()
        receipt_id = uuid4()
        line_id = uuid4()

        mock_received = MagicMock()
        mock_inspecting = MagicMock()
        mock_status_class.RECEIVED = mock_received
        mock_status_class.INSPECTING = mock_inspecting

        mock_receipt = MockGoodsReceipt(receipt_id=receipt_id, organization_id=org_id)
        mock_receipt.status = mock_inspecting

        mock_line = MockGoodsReceiptLine(
            line_id=line_id,
            receipt_id=receipt_id,
            quantity_received=Decimal("10"),
        )

        db.query.return_value.filter.return_value.first.side_effect = [
            mock_receipt,
            mock_line,
        ]

        inspection_results = [
            InspectionResult(
                line_id=line_id,
                quantity_accepted=Decimal("5"),
                quantity_rejected=Decimal("3"),  # Total is 8, should be 10
            )
        ]

        with pytest.raises(HTTPException) as exc_info:
            GoodsReceiptService.complete_inspection(
                db, org_id, receipt_id, inspection_results
            )

        assert exc_info.value.status_code == 400
        assert "must equal received quantity" in str(exc_info.value.detail)

    @patch("app.services.ifrs.ap.goods_receipt.ReceiptStatus")
    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceiptLine")
    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceipt")
    def test_complete_inspection_line_not_found(
        self, mock_receipt_class, mock_line_class, mock_status_class
    ):
        """Test inspection fails when line not found."""
        db = MagicMock()
        org_id = uuid4()
        receipt_id = uuid4()

        mock_received = MagicMock()
        mock_inspecting = MagicMock()
        mock_status_class.RECEIVED = mock_received
        mock_status_class.INSPECTING = mock_inspecting

        mock_receipt = MockGoodsReceipt(receipt_id=receipt_id, organization_id=org_id)
        mock_receipt.status = mock_inspecting

        db.query.return_value.filter.return_value.first.side_effect = [
            mock_receipt,
            None,  # Line not found
        ]

        inspection_results = [
            InspectionResult(
                line_id=uuid4(),
                quantity_accepted=Decimal("10"),
                quantity_rejected=Decimal("0"),
            )
        ]

        with pytest.raises(HTTPException) as exc_info:
            GoodsReceiptService.complete_inspection(
                db, org_id, receipt_id, inspection_results
            )

        assert exc_info.value.status_code == 400
        assert "Receipt line" in str(exc_info.value.detail)


# ===================== ACCEPT ALL TESTS =====================

class TestAcceptAll:
    """Tests for accepting all items."""

    @patch("app.services.ifrs.ap.goods_receipt.ReceiptStatus")
    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceipt")
    def test_accept_all_success(self, mock_receipt_class, mock_status_class):
        """Test successful accept all."""
        db = MagicMock()
        org_id = uuid4()
        receipt_id = uuid4()

        mock_received = MagicMock()
        mock_inspecting = MagicMock()
        mock_accepted = MagicMock()
        mock_status_class.RECEIVED = mock_received
        mock_status_class.INSPECTING = mock_inspecting
        mock_status_class.ACCEPTED = mock_accepted

        mock_line1 = MockGoodsReceiptLine(quantity_received=Decimal("10"))
        mock_line2 = MockGoodsReceiptLine(quantity_received=Decimal("5"))

        mock_receipt = MockGoodsReceipt(
            receipt_id=receipt_id,
            organization_id=org_id,
            lines=[mock_line1, mock_line2],
        )
        mock_receipt.status = mock_received

        db.query.return_value.filter.return_value.first.return_value = mock_receipt

        result = GoodsReceiptService.accept_all(db, org_id, receipt_id)

        assert result is not None
        assert mock_receipt.status == mock_accepted
        assert mock_line1.quantity_accepted == Decimal("10")
        assert mock_line1.quantity_rejected == Decimal("0")
        assert mock_line2.quantity_accepted == Decimal("5")
        db.commit.assert_called_once()

    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceipt")
    def test_accept_all_not_found(self, mock_receipt_class):
        """Test accept all on non-existent receipt."""
        db = MagicMock()
        org_id = uuid4()

        db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            GoodsReceiptService.accept_all(db, org_id, uuid4())

        assert exc_info.value.status_code == 404

    @patch("app.services.ifrs.ap.goods_receipt.ReceiptStatus")
    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceipt")
    def test_accept_all_wrong_status(self, mock_receipt_class, mock_status_class):
        """Test accept all on receipt not in correct status."""
        db = MagicMock()
        org_id = uuid4()
        receipt_id = uuid4()

        mock_received = MagicMock()
        mock_inspecting = MagicMock()
        mock_rejected = MagicMock()
        mock_rejected.value = "REJECTED"
        mock_status_class.RECEIVED = mock_received
        mock_status_class.INSPECTING = mock_inspecting

        mock_receipt = MockGoodsReceipt(receipt_id=receipt_id, organization_id=org_id)
        mock_receipt.status = mock_rejected

        db.query.return_value.filter.return_value.first.return_value = mock_receipt

        with pytest.raises(HTTPException) as exc_info:
            GoodsReceiptService.accept_all(db, org_id, receipt_id)

        assert exc_info.value.status_code == 400
        assert "Cannot accept" in str(exc_info.value.detail)


# ===================== INTERNAL METHODS TESTS =====================

class TestInternalMethods:
    """Tests for internal helper methods."""

    @patch("app.services.ifrs.ap.goods_receipt.POStatus")
    def test_update_po_status_partially_received(self, mock_po_status):
        """Test PO status update for partial receipt."""
        db = MagicMock()

        mock_partial = MagicMock()
        mock_received = MagicMock()
        mock_po_status.PARTIALLY_RECEIVED = mock_partial
        mock_po_status.RECEIVED = mock_received

        # PO line with partial receipt
        mock_line = MockPurchaseOrderLine(
            quantity_ordered=Decimal("10"),
            quantity_received=Decimal("5"),
            unit_price=Decimal("100.00"),
        )
        mock_po = MockPurchaseOrder(lines=[mock_line])

        GoodsReceiptService._update_po_status(db, mock_po)

        assert mock_po.amount_received == Decimal("500.00")
        assert mock_po.status == mock_partial

    @patch("app.services.ifrs.ap.goods_receipt.POStatus")
    def test_update_po_status_fully_received(self, mock_po_status):
        """Test PO status update for full receipt."""
        db = MagicMock()

        mock_partial = MagicMock()
        mock_received = MagicMock()
        mock_po_status.PARTIALLY_RECEIVED = mock_partial
        mock_po_status.RECEIVED = mock_received

        # PO line fully received
        mock_line = MockPurchaseOrderLine(
            quantity_ordered=Decimal("10"),
            quantity_received=Decimal("10"),
            unit_price=Decimal("100.00"),
        )
        mock_po = MockPurchaseOrder(lines=[mock_line])

        GoodsReceiptService._update_po_status(db, mock_po)

        assert mock_po.amount_received == Decimal("1000.00")
        assert mock_po.status == mock_received

    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceiptService._update_po_status")
    @patch("app.services.ifrs.ap.goods_receipt.PurchaseOrder")
    @patch("app.services.ifrs.ap.goods_receipt.PurchaseOrderLine")
    def test_reverse_po_quantities(
        self, mock_po_line_class, mock_po_class, mock_update_status
    ):
        """Test reversing PO quantities for rejected receipt."""
        db = MagicMock()
        po_id = uuid4()
        po_line_id = uuid4()

        mock_po_line = MockPurchaseOrderLine(
            line_id=po_line_id,
            quantity_received=Decimal("10"),
        )
        mock_po = MockPurchaseOrder(po_id=po_id)

        mock_receipt_line = MockGoodsReceiptLine(
            po_line_id=po_line_id,
            quantity_received=Decimal("5"),
        )
        mock_receipt = MockGoodsReceipt(po_id=po_id, lines=[mock_receipt_line])

        db.query.return_value.filter.return_value.first.side_effect = [
            mock_po_line,
            mock_po,
        ]

        GoodsReceiptService._reverse_po_quantities(db, mock_receipt)

        assert mock_po_line.quantity_received == Decimal("5")  # Reduced by 5
        mock_update_status.assert_called_once()


# ===================== GETTER TESTS =====================

class TestGetters:
    """Tests for getter methods."""

    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceipt")
    def test_get_receipt(self, mock_receipt_class):
        """Test getting receipt by ID."""
        db = MagicMock()
        receipt_id = uuid4()

        mock_receipt = MockGoodsReceipt(receipt_id=receipt_id)
        db.query.return_value.filter.return_value.first.return_value = mock_receipt

        result = GoodsReceiptService.get(db, str(receipt_id))

        assert result is not None
        assert result.receipt_id == receipt_id

    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceipt")
    def test_get_receipt_not_found(self, mock_receipt_class):
        """Test getting non-existent receipt."""
        db = MagicMock()

        db.query.return_value.filter.return_value.first.return_value = None

        result = GoodsReceiptService.get(db, str(uuid4()))

        assert result is None

    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceipt")
    def test_get_by_number(self, mock_receipt_class):
        """Test getting receipt by number."""
        db = MagicMock()
        org_id = uuid4()

        mock_receipt = MockGoodsReceipt(receipt_number="GR-000001")
        db.query.return_value.filter.return_value.first.return_value = mock_receipt

        result = GoodsReceiptService.get_by_number(db, org_id, "GR-000001")

        assert result is not None
        assert result.receipt_number == "GR-000001"

    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceiptLine")
    def test_get_receipt_lines(self, mock_line_class):
        """Test getting receipt lines."""
        db = MagicMock()
        receipt_id = uuid4()

        lines = [
            MockGoodsReceiptLine(line_number=1),
            MockGoodsReceiptLine(line_number=2),
        ]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = lines

        result = GoodsReceiptService.get_receipt_lines(db, str(receipt_id))

        assert len(result) == 2

    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceipt")
    def test_list_by_po(self, mock_receipt_class):
        """Test listing receipts by PO."""
        db = MagicMock()
        org_id = uuid4()
        po_id = uuid4()

        receipts = [
            MockGoodsReceipt(receipt_number="GR-000001"),
            MockGoodsReceipt(receipt_number="GR-000002"),
        ]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = receipts

        result = GoodsReceiptService.list_by_po(db, org_id, po_id)

        assert len(result) == 2


# ===================== LIST TESTS =====================

class TestListReceipts:
    """Tests for listing goods receipts."""

    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceipt")
    def test_list_receipts(self, mock_receipt_class):
        """Test listing goods receipts."""
        db = MagicMock()

        receipts = [
            MockGoodsReceipt(receipt_number="GR-000001"),
            MockGoodsReceipt(receipt_number="GR-000002"),
        ]
        db.query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = receipts

        result = GoodsReceiptService.list(db)

        assert len(result) == 2

    def test_list_receipts_with_filters(self):
        """Test listing receipts with filters."""
        db = MagicMock()
        org_id = uuid4()
        supplier_id = uuid4()

        receipts = [MockGoodsReceipt()]

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = receipts
        db.query.return_value = mock_query

        result = GoodsReceiptService.list(
            db,
            organization_id=str(org_id),
            supplier_id=str(supplier_id),
            status=MockReceiptStatus.ACCEPTED,
            from_date=date(2024, 1, 1),
            to_date=date(2024, 12, 31),
            limit=10,
            offset=0,
        )

        assert len(result) == 1
        assert mock_query.filter.called

    @patch("app.services.ifrs.ap.goods_receipt.GoodsReceipt")
    def test_list_receipts_empty(self, mock_receipt_class):
        """Test listing returns empty when no receipts."""
        db = MagicMock()

        db.query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []

        result = GoodsReceiptService.list(db)

        assert len(result) == 0
