"""
Tests for SalesOrderService.

Tests sales order creation, workflow, shipment, and invoicing.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.models.finance.ar.sales_order import FulfillmentStatus, SOStatus
from app.services.finance.ar.sales_order import SalesOrderService


class MockSalesOrder:
    """Mock SalesOrder model."""

    def __init__(
        self,
        so_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        so_number: str = "SO-001",
        customer_id: uuid.UUID = None,
        order_date: date = None,
        status: SOStatus = SOStatus.DRAFT,
        currency_code: str = "USD",
        exchange_rate: Decimal = Decimal("1"),
        subtotal: Decimal = Decimal("0"),
        discount_amount: Decimal = Decimal("0"),
        tax_amount: Decimal = Decimal("0"),
        shipping_amount: Decimal = Decimal("0"),
        total_amount: Decimal = Decimal("0"),
        invoiced_amount: Decimal = Decimal("0"),
        lines: list = None,
        shipments: list = None,
        payment_terms_id: uuid.UUID = None,
        ship_to_name: str = None,
        ship_to_address: str = None,
        shipping_method: str = None,
        submitted_by: uuid.UUID = None,
        submitted_at: datetime = None,
        approved_by: uuid.UUID = None,
        approved_at: datetime = None,
        confirmed_at: datetime = None,
        completed_at: datetime = None,
        cancelled_at: datetime = None,
        cancellation_reason: str = None,
        updated_by: uuid.UUID = None,
        updated_at: datetime = None,
        customer_po_number: str = None,
        created_by: uuid.UUID = None,
    ):
        self.so_id = so_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.so_number = so_number
        self.customer_id = customer_id or uuid.uuid4()
        self.order_date = order_date or date.today()
        self.status = status
        self.currency_code = currency_code
        self.exchange_rate = exchange_rate
        self.subtotal = subtotal
        self.discount_amount = discount_amount
        self.tax_amount = tax_amount
        self.shipping_amount = shipping_amount
        self.total_amount = total_amount
        self.invoiced_amount = invoiced_amount
        self.lines = lines or []
        self.shipments = shipments or []
        self.payment_terms_id = payment_terms_id
        self.ship_to_name = ship_to_name
        self.ship_to_address = ship_to_address
        self.shipping_method = shipping_method
        self.submitted_by = submitted_by
        self.submitted_at = submitted_at
        self.approved_by = approved_by
        self.approved_at = approved_at
        self.confirmed_at = confirmed_at
        self.completed_at = completed_at
        self.cancelled_at = cancelled_at
        self.cancellation_reason = cancellation_reason
        self.updated_by = updated_by
        self.updated_at = updated_at
        self.customer_po_number = customer_po_number
        self.created_by = created_by

    @property
    def is_fully_shipped(self):
        if not self.lines:
            return False
        return all(
            line.quantity_shipped >= line.quantity_ordered for line in self.lines
        )

    @property
    def is_fully_invoiced(self):
        if not self.lines:
            return False
        return all(
            line.quantity_invoiced >= line.quantity_ordered for line in self.lines
        )


class MockSalesOrderLine:
    """Mock SalesOrderLine model."""

    def __init__(
        self,
        line_id: uuid.UUID = None,
        so_id: uuid.UUID = None,
        line_number: int = 1,
        item_id: uuid.UUID = None,
        description: str = "Test Item",
        quantity_ordered: Decimal = Decimal("10"),
        quantity_shipped: Decimal = Decimal("0"),
        quantity_invoiced: Decimal = Decimal("0"),
        unit_price: Decimal = Decimal("100"),
        discount_percent: Decimal = Decimal("0"),
        discount_amount: Decimal = Decimal("0"),
        tax_code_id: uuid.UUID = None,
        tax_amount: Decimal = Decimal("0"),
        line_total: Decimal = None,
        revenue_account_id: uuid.UUID = None,
        project_id: uuid.UUID = None,
        cost_center_id: uuid.UUID = None,
        fulfillment_status: FulfillmentStatus = FulfillmentStatus.PENDING,
    ):
        self.line_id = line_id or uuid.uuid4()
        self.so_id = so_id or uuid.uuid4()
        self.line_number = line_number
        self.item_id = item_id
        self.description = description
        self.quantity_ordered = quantity_ordered
        self.quantity_shipped = quantity_shipped
        self.quantity_invoiced = quantity_invoiced
        self.unit_price = unit_price
        self.discount_percent = discount_percent
        self.discount_amount = discount_amount
        self.tax_code_id = tax_code_id
        self.tax_amount = tax_amount
        self.line_total = line_total or (
            quantity_ordered * unit_price - discount_amount + tax_amount
        )
        self.revenue_account_id = revenue_account_id
        self.project_id = project_id
        self.cost_center_id = cost_center_id
        self.fulfillment_status = fulfillment_status


class MockShipment:
    """Mock Shipment model."""

    def __init__(
        self,
        shipment_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        shipment_number: str = "SHP-001",
        so_id: uuid.UUID = None,
        shipment_date: date = None,
        is_delivered: bool = False,
        delivered_at: datetime = None,
    ):
        self.shipment_id = shipment_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.shipment_number = shipment_number
        self.so_id = so_id
        self.shipment_date = shipment_date or date.today()
        self.is_delivered = is_delivered
        self.delivered_at = delivered_at


class TestGenerateSONumber:
    """Tests for generate_so_number method."""

    @patch("app.services.finance.ar.sales_order.SyncNumberingService")
    def test_generate_so_number(self, mock_numbering_class):
        """Test SO number generation."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()

        mock_numbering = MagicMock()
        mock_numbering.generate_next_number.return_value = "SO-2024-001"
        mock_numbering_class.return_value = mock_numbering

        result = SalesOrderService.generate_so_number(mock_db, org_id)

        assert result == "SO-2024-001"
        mock_numbering.generate_next_number.assert_called_once()


class TestCreate:
    """Tests for create method."""

    @patch("app.services.finance.ar.sales_order.SalesOrder")
    @patch("app.services.finance.ar.sales_order.SalesOrderService.generate_so_number")
    def test_create_basic_order(self, mock_generate, mock_so_class):
        """Test creating a basic sales order."""
        mock_db = MagicMock()
        org_id = str(uuid.uuid4())
        customer_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())

        mock_generate.return_value = "SO-001"
        mock_so = MockSalesOrder()
        mock_so.lines = []
        mock_so_class.return_value = mock_so

        SalesOrderService.create(
            db=mock_db,
            organization_id=org_id,
            customer_id=customer_id,
            order_date=date.today(),
            created_by=user_id,
        )

        mock_db.add.assert_called_once()
        mock_db.flush.assert_called()

    @patch("app.services.finance.ar.sales_order.SalesOrder")
    @patch("app.services.finance.ar.sales_order.SalesOrderService.generate_so_number")
    @patch("app.services.finance.ar.sales_order.SalesOrderService._add_lines")
    @patch("app.services.finance.ar.sales_order.SalesOrderService._recalculate_totals")
    def test_create_order_with_lines(
        self, mock_recalc, mock_add_lines, mock_generate, mock_so_class
    ):
        """Test creating a sales order with lines."""
        mock_db = MagicMock()
        org_id = str(uuid.uuid4())
        customer_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())

        mock_generate.return_value = "SO-001"
        mock_so = MockSalesOrder()
        mock_so.lines = []
        mock_so_class.return_value = mock_so

        lines = [
            {
                "item_id": str(uuid.uuid4()),
                "description": "Test Item",
                "quantity": 10,
                "unit_price": "100.00",
            }
        ]

        SalesOrderService.create(
            db=mock_db,
            organization_id=org_id,
            customer_id=customer_id,
            order_date=date.today(),
            created_by=user_id,
            lines=lines,
        )

        mock_add_lines.assert_called_once()
        mock_recalc.assert_called_once()

    @patch("app.services.finance.ar.sales_order.SalesOrder")
    @patch("app.services.finance.ar.sales_order.SalesOrderService.generate_so_number")
    def test_create_order_with_shipping_details(self, mock_generate, mock_so_class):
        """Test creating order with shipping details."""
        mock_db = MagicMock()
        org_id = str(uuid.uuid4())
        customer_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())

        mock_generate.return_value = "SO-001"
        mock_so = MockSalesOrder()
        mock_so.lines = []
        mock_so_class.return_value = mock_so

        SalesOrderService.create(
            db=mock_db,
            organization_id=org_id,
            customer_id=customer_id,
            order_date=date.today(),
            created_by=user_id,
            ship_to_name="John Doe",
            ship_to_address="123 Main St",
            ship_to_city="New York",
            ship_to_country="USA",
            shipping_method="Express",
        )

        mock_db.add.assert_called_once()


class TestSubmit:
    """Tests for submit method."""

    def test_submit_draft_order(self):
        """Test submitting a draft order."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()
        user_id = uuid.uuid4()

        mock_so = MockSalesOrder(so_id=so_id, status=SOStatus.DRAFT)
        mock_db.get.return_value = mock_so

        result = SalesOrderService.submit(
            db=mock_db,
            so_id=str(so_id),
            submitted_by=str(user_id),
        )

        assert result.status == SOStatus.SUBMITTED
        assert result.submitted_by is not None
        mock_db.flush.assert_called()

    def test_submit_not_found(self):
        """Test submitting non-existent order."""
        mock_db = MagicMock()
        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc_info:
            SalesOrderService.submit(
                db=mock_db,
                so_id=str(uuid.uuid4()),
                submitted_by=str(uuid.uuid4()),
            )

        assert "not found" in str(exc_info.value)

    def test_submit_wrong_status(self):
        """Test submitting order in wrong status."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()

        mock_so = MockSalesOrder(so_id=so_id, status=SOStatus.APPROVED)
        mock_db.get.return_value = mock_so

        with pytest.raises(ValueError) as exc_info:
            SalesOrderService.submit(
                db=mock_db,
                so_id=str(so_id),
                submitted_by=str(uuid.uuid4()),
            )

        assert "Cannot submit" in str(exc_info.value)


class TestApprove:
    """Tests for approve method."""

    def test_approve_submitted_order(self):
        """Test approving a submitted order."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()
        user_id = uuid.uuid4()

        mock_so = MockSalesOrder(so_id=so_id, status=SOStatus.SUBMITTED)
        mock_db.get.return_value = mock_so

        result = SalesOrderService.approve(
            db=mock_db,
            so_id=str(so_id),
            approved_by=str(user_id),
        )

        assert result.status == SOStatus.APPROVED
        assert result.approved_by is not None
        mock_db.flush.assert_called()

    def test_approve_not_found(self):
        """Test approving non-existent order."""
        mock_db = MagicMock()
        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc_info:
            SalesOrderService.approve(
                db=mock_db,
                so_id=str(uuid.uuid4()),
                approved_by=str(uuid.uuid4()),
            )

        assert "not found" in str(exc_info.value)

    def test_approve_wrong_status(self):
        """Test approving order in wrong status."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()

        mock_so = MockSalesOrder(so_id=so_id, status=SOStatus.DRAFT)
        mock_db.get.return_value = mock_so

        with pytest.raises(ValueError) as exc_info:
            SalesOrderService.approve(
                db=mock_db,
                so_id=str(so_id),
                approved_by=str(uuid.uuid4()),
            )

        assert "Cannot approve" in str(exc_info.value)


class TestConfirm:
    """Tests for confirm method."""

    def test_confirm_approved_order(self):
        """Test confirming an approved order."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()

        mock_so = MockSalesOrder(so_id=so_id, status=SOStatus.APPROVED)
        mock_db.get.return_value = mock_so

        result = SalesOrderService.confirm(
            db=mock_db,
            so_id=str(so_id),
        )

        assert result.status == SOStatus.CONFIRMED
        mock_db.flush.assert_called()

    def test_confirm_not_found(self):
        """Test confirming non-existent order."""
        mock_db = MagicMock()
        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc_info:
            SalesOrderService.confirm(
                db=mock_db,
                so_id=str(uuid.uuid4()),
            )

        assert "not found" in str(exc_info.value)

    def test_confirm_wrong_status(self):
        """Test confirming order in wrong status."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()

        mock_so = MockSalesOrder(so_id=so_id, status=SOStatus.DRAFT)
        mock_db.get.return_value = mock_so

        with pytest.raises(ValueError) as exc_info:
            SalesOrderService.confirm(
                db=mock_db,
                so_id=str(so_id),
            )

        assert "Cannot confirm" in str(exc_info.value)

    @patch(
        "app.services.finance.ar.sales_order.SalesOrderService._reserve_stock_on_confirm"
    )
    def test_confirm_triggers_reservation_hook(self, mock_reserve_hook):
        """Test confirmation calls stock reservation hook."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()
        mock_so = MockSalesOrder(so_id=so_id, status=SOStatus.APPROVED)
        mock_db.get.return_value = mock_so

        SalesOrderService.confirm(
            db=mock_db,
            so_id=str(so_id),
        )

        mock_reserve_hook.assert_called_once_with(mock_db, mock_so)

    @patch("app.services.hooks.emit_hook_event")
    @patch(
        "app.services.finance.ar.sales_order.SalesOrderService._reserve_stock_on_confirm"
    )
    def test_confirm_emits_service_hook(self, _mock_reserve_hook, mock_emit_hook):
        """Test confirmation emits sales.order.confirmed hook event."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()
        mock_so = MockSalesOrder(so_id=so_id, status=SOStatus.APPROVED)
        mock_db.get.return_value = mock_so

        SalesOrderService.confirm(
            db=mock_db,
            so_id=str(so_id),
        )

        mock_emit_hook.assert_called_once()


class TestCreateShipment:
    """Tests for create_shipment method."""

    @patch("app.services.finance.ar.sales_order.SyncNumberingService")
    @patch("app.services.finance.ar.sales_order.Shipment")
    @patch("app.services.finance.ar.sales_order.ShipmentLine")
    def test_create_shipment_success(
        self, mock_ship_line_class, mock_shipment_class, mock_numbering_class
    ):
        """Test creating a shipment."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()
        user_id = uuid.uuid4()
        line_id = uuid.uuid4()

        mock_so_line = MockSalesOrderLine(
            line_id=line_id,
            so_id=so_id,
            quantity_ordered=Decimal("10"),
            quantity_shipped=Decimal("0"),
        )

        mock_so = MockSalesOrder(
            so_id=so_id,
            status=SOStatus.CONFIRMED,
            lines=[mock_so_line],
        )

        mock_shipment = MockShipment(so_id=so_id)
        mock_shipment_class.return_value = mock_shipment

        mock_numbering = MagicMock()
        mock_numbering.generate_next_number.return_value = "SHP-001"
        mock_numbering_class.return_value = mock_numbering

        def mock_get(model_class, id_val):
            model_str = str(model_class)
            if "SalesOrder" in model_str and "Line" not in model_str:
                return mock_so
            elif "SalesOrderLine" in model_str:
                return mock_so_line
            return None

        mock_db.get.side_effect = mock_get

        SalesOrderService.create_shipment(
            db=mock_db,
            so_id=str(so_id),
            shipment_date=date.today(),
            created_by=str(user_id),
            line_quantities=[{"line_id": str(line_id), "quantity": 5}],
        )

        assert mock_db.add.called
        mock_db.flush.assert_called()

    def test_create_shipment_order_not_found(self):
        """Test creating shipment for non-existent order."""
        mock_db = MagicMock()
        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc_info:
            SalesOrderService.create_shipment(
                db=mock_db,
                so_id=str(uuid.uuid4()),
                shipment_date=date.today(),
                created_by=str(uuid.uuid4()),
                line_quantities=[],
            )

        assert "not found" in str(exc_info.value)

    def test_create_shipment_wrong_status(self):
        """Test creating shipment for order in wrong status."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()

        mock_so = MockSalesOrder(so_id=so_id, status=SOStatus.DRAFT)
        mock_db.get.return_value = mock_so

        with pytest.raises(ValueError) as exc_info:
            SalesOrderService.create_shipment(
                db=mock_db,
                so_id=str(so_id),
                shipment_date=date.today(),
                created_by=str(uuid.uuid4()),
                line_quantities=[],
            )

        assert "Cannot ship" in str(exc_info.value)

    @patch("app.services.finance.ar.sales_order.SyncNumberingService")
    @patch("app.services.finance.ar.sales_order.Shipment")
    def test_create_shipment_over_quantity(
        self, mock_shipment_class, mock_numbering_class
    ):
        """Test creating shipment with more than available quantity."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()
        line_id = uuid.uuid4()

        mock_so_line = MockSalesOrderLine(
            line_id=line_id,
            so_id=so_id,
            quantity_ordered=Decimal("10"),
            quantity_shipped=Decimal("8"),  # Only 2 remaining
        )

        mock_so = MockSalesOrder(
            so_id=so_id,
            status=SOStatus.CONFIRMED,
            lines=[mock_so_line],
        )

        mock_numbering = MagicMock()
        mock_numbering.generate_next_number.return_value = "SHP-001"
        mock_numbering_class.return_value = mock_numbering

        mock_shipment = MockShipment(so_id=so_id)
        mock_shipment_class.return_value = mock_shipment

        def mock_get(model_class, id_val):
            model_str = str(model_class)
            if "SalesOrder" in model_str and "Line" not in model_str:
                return mock_so
            elif "SalesOrderLine" in model_str:
                return mock_so_line
            return None

        mock_db.get.side_effect = mock_get

        with pytest.raises(ValueError) as exc_info:
            SalesOrderService.create_shipment(
                db=mock_db,
                so_id=str(so_id),
                shipment_date=date.today(),
                created_by=str(uuid.uuid4()),
                line_quantities=[
                    {"line_id": str(line_id), "quantity": 5}
                ],  # Try to ship 5
            )

        assert "Cannot ship" in str(exc_info.value)

    @patch("app.services.inventory.stock_reservation.StockReservationService")
    @patch("app.services.finance.ar.sales_order.is_feature_enabled")
    @patch("app.services.finance.ar.sales_order.SyncNumberingService")
    @patch("app.services.finance.ar.sales_order.Shipment")
    @patch("app.services.finance.ar.sales_order.ShipmentLine")
    def test_create_shipment_fulfills_reservation(
        self,
        _mock_ship_line_class,
        mock_shipment_class,
        mock_numbering_class,
        mock_feature_enabled,
        mock_reservation_service_class,
    ):
        """Test shipment fulfillment updates reservation when enabled."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()
        line_id = uuid.uuid4()

        mock_so_line = MockSalesOrderLine(
            line_id=line_id,
            so_id=so_id,
            quantity_ordered=Decimal("10"),
            quantity_shipped=Decimal("0"),
        )
        mock_so = MockSalesOrder(
            so_id=so_id,
            status=SOStatus.CONFIRMED,
            lines=[mock_so_line],
        )
        mock_shipment = MockShipment(so_id=so_id)
        mock_shipment_class.return_value = mock_shipment

        mock_numbering = MagicMock()
        mock_numbering.generate_next_number.return_value = "SHP-001"
        mock_numbering_class.return_value = mock_numbering
        mock_feature_enabled.return_value = True

        mock_reservation = MagicMock(reservation_id=uuid.uuid4())
        mock_reservation_service = MagicMock()
        mock_reservation_service.get_reservation_for_line.return_value = (
            mock_reservation
        )
        mock_reservation_service_class.return_value = mock_reservation_service

        def mock_get(model_class, _id):
            model_str = str(model_class)
            if "SalesOrder" in model_str and "Line" not in model_str:
                return mock_so
            if "SalesOrderLine" in model_str:
                return mock_so_line
            return None

        mock_db.get.side_effect = mock_get

        SalesOrderService.create_shipment(
            db=mock_db,
            so_id=str(so_id),
            shipment_date=date.today(),
            created_by=str(uuid.uuid4()),
            line_quantities=[{"line_id": str(line_id), "quantity": 4}],
        )

        mock_reservation_service.fulfill.assert_called_once_with(
            mock_reservation.reservation_id,
            Decimal("4"),
        )

    @patch("app.services.hooks.emit_hook_event")
    @patch("app.services.finance.ar.sales_order.SyncNumberingService")
    @patch("app.services.finance.ar.sales_order.Shipment")
    @patch("app.services.finance.ar.sales_order.ShipmentLine")
    def test_create_shipment_emits_service_hook(
        self,
        _mock_ship_line_class,
        mock_shipment_class,
        mock_numbering_class,
        mock_emit_hook,
    ):
        """Test shipment creation emits shipment.created hook event."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()
        line_id = uuid.uuid4()

        mock_so_line = MockSalesOrderLine(
            line_id=line_id,
            so_id=so_id,
            quantity_ordered=Decimal("10"),
            quantity_shipped=Decimal("0"),
        )
        mock_so = MockSalesOrder(
            so_id=so_id,
            status=SOStatus.CONFIRMED,
            lines=[mock_so_line],
        )
        mock_shipment = MockShipment(so_id=so_id)
        mock_shipment_class.return_value = mock_shipment

        mock_numbering = MagicMock()
        mock_numbering.generate_next_number.return_value = "SHP-001"
        mock_numbering_class.return_value = mock_numbering

        def mock_get(model_class, _id):
            model_str = str(model_class)
            if "SalesOrder" in model_str and "Line" not in model_str:
                return mock_so
            if "SalesOrderLine" in model_str:
                return mock_so_line
            return None

        mock_db.get.side_effect = mock_get

        SalesOrderService.create_shipment(
            db=mock_db,
            so_id=str(so_id),
            shipment_date=date.today(),
            created_by=str(uuid.uuid4()),
            line_quantities=[{"line_id": str(line_id), "quantity": 4}],
        )

        mock_emit_hook.assert_called()


class TestMarkDelivered:
    """Tests for mark_delivered method."""

    def test_mark_delivered_success(self):
        """Test marking shipment as delivered."""
        mock_db = MagicMock()
        shipment_id = uuid.uuid4()

        mock_shipment = MockShipment(shipment_id=shipment_id)
        mock_db.get.return_value = mock_shipment

        result = SalesOrderService.mark_delivered(
            db=mock_db,
            shipment_id=str(shipment_id),
        )

        assert result.is_delivered is True
        assert result.delivered_at is not None
        mock_db.flush.assert_called_once()

    def test_mark_delivered_not_found(self):
        """Test marking non-existent shipment as delivered."""
        mock_db = MagicMock()
        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc_info:
            SalesOrderService.mark_delivered(
                db=mock_db,
                shipment_id=str(uuid.uuid4()),
            )

        assert "not found" in str(exc_info.value)


class TestCreateInvoiceFromSO:
    """Tests for create_invoice_from_so method."""

    @patch("app.services.finance.ar.sales_order.InvoiceLine")
    @patch("app.services.finance.ar.sales_order.Invoice")
    @patch("app.services.finance.ar.sales_order.SyncNumberingService")
    def test_create_invoice_success(
        self, mock_numbering_class, mock_invoice_class, mock_inv_line_class
    ):
        """Test creating invoice from shipped order."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()
        user_id = uuid.uuid4()
        line_id = uuid.uuid4()

        mock_so_line = MockSalesOrderLine(
            line_id=line_id,
            so_id=so_id,
            quantity_ordered=Decimal("10"),
            quantity_shipped=Decimal("10"),
            quantity_invoiced=Decimal("0"),
            unit_price=Decimal("100"),
        )

        mock_so = MockSalesOrder(
            so_id=so_id,
            status=SOStatus.SHIPPED,
            lines=[mock_so_line],
        )

        mock_invoice = MagicMock()
        mock_invoice.invoice_id = uuid.uuid4()
        mock_invoice.total_amount = Decimal("1000")
        mock_invoice_class.return_value = mock_invoice

        # Configure SyncNumberingService mock properly
        mock_numbering_instance = MagicMock()
        mock_numbering_instance.generate_next_number.return_value = "INV-001"
        mock_numbering_class.return_value = mock_numbering_instance

        mock_db.get.return_value = mock_so

        SalesOrderService.create_invoice_from_so(
            db=mock_db,
            so_id=str(so_id),
            created_by=str(user_id),
        )

        assert mock_db.add.called
        mock_db.flush.assert_called()

    def test_create_invoice_order_not_found(self):
        """Test creating invoice for non-existent order."""
        mock_db = MagicMock()
        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc_info:
            SalesOrderService.create_invoice_from_so(
                db=mock_db,
                so_id=str(uuid.uuid4()),
                created_by=str(uuid.uuid4()),
            )

        assert "not found" in str(exc_info.value)

    def test_create_invoice_wrong_status(self):
        """Test creating invoice for order in wrong status."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()

        mock_so = MockSalesOrder(so_id=so_id, status=SOStatus.DRAFT)
        mock_db.get.return_value = mock_so

        with pytest.raises(ValueError) as exc_info:
            SalesOrderService.create_invoice_from_so(
                db=mock_db,
                so_id=str(so_id),
                created_by=str(uuid.uuid4()),
            )

        assert "Cannot invoice" in str(exc_info.value)

    @patch("app.services.finance.ar.sales_order.SyncNumberingService")
    def test_create_invoice_no_lines_to_invoice(self, mock_numbering_class):
        """Test creating invoice when no lines available."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()

        # Configure SyncNumberingService mock
        mock_numbering_instance = MagicMock()
        mock_numbering_instance.generate_next_number.return_value = "INV-001"
        mock_numbering_class.return_value = mock_numbering_instance

        mock_so_line = MockSalesOrderLine(
            so_id=so_id,
            quantity_ordered=Decimal("10"),
            quantity_shipped=Decimal("10"),
            quantity_invoiced=Decimal("10"),  # Already fully invoiced
        )

        mock_so = MockSalesOrder(
            so_id=so_id,
            status=SOStatus.SHIPPED,
            lines=[mock_so_line],
        )

        mock_db.get.return_value = mock_so

        with pytest.raises(ValueError) as exc_info:
            SalesOrderService.create_invoice_from_so(
                db=mock_db,
                so_id=str(so_id),
                created_by=str(uuid.uuid4()),
            )

        assert "No lines to invoice" in str(exc_info.value)


class TestCancel:
    """Tests for cancel method."""

    def test_cancel_draft_order(self):
        """Test cancelling a draft order."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()
        user_id = uuid.uuid4()

        mock_so_line = MockSalesOrderLine(so_id=so_id)
        mock_so = MockSalesOrder(
            so_id=so_id,
            status=SOStatus.DRAFT,
            lines=[mock_so_line],
            shipments=[],
        )
        mock_db.get.return_value = mock_so

        result = SalesOrderService.cancel(
            db=mock_db,
            so_id=str(so_id),
            cancelled_by=str(user_id),
            reason="Customer request",
        )

        assert result.status == SOStatus.CANCELLED
        assert mock_so_line.fulfillment_status == FulfillmentStatus.CANCELLED
        mock_db.flush.assert_called()

    def test_cancel_not_found(self):
        """Test cancelling non-existent order."""
        mock_db = MagicMock()
        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc_info:
            SalesOrderService.cancel(
                db=mock_db,
                so_id=str(uuid.uuid4()),
                cancelled_by=str(uuid.uuid4()),
            )

        assert "not found" in str(exc_info.value)

    def test_cancel_shipped_order(self):
        """Test cancelling shipped order fails."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()

        mock_so = MockSalesOrder(so_id=so_id, status=SOStatus.SHIPPED)
        mock_db.get.return_value = mock_so

        with pytest.raises(ValueError) as exc_info:
            SalesOrderService.cancel(
                db=mock_db,
                so_id=str(so_id),
                cancelled_by=str(uuid.uuid4()),
            )

        assert "Cannot cancel" in str(exc_info.value)

    def test_cancel_order_with_shipments(self):
        """Test cancelling order with shipments fails."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()

        mock_so = MockSalesOrder(
            so_id=so_id,
            status=SOStatus.CONFIRMED,
            shipments=[MockShipment()],  # Has shipments
        )
        mock_db.get.return_value = mock_so

        with pytest.raises(ValueError) as exc_info:
            SalesOrderService.cancel(
                db=mock_db,
                so_id=str(so_id),
                cancelled_by=str(uuid.uuid4()),
            )

        assert "existing shipments" in str(exc_info.value)

    @patch("app.services.inventory.stock_reservation.StockReservationService")
    @patch("app.services.finance.ar.sales_order.is_feature_enabled")
    def test_cancel_releases_reservations(
        self,
        mock_feature_enabled,
        mock_reservation_service_class,
    ):
        """Test cancelling SO releases linked reservations."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()
        mock_so_line = MockSalesOrderLine(so_id=so_id)
        mock_so = MockSalesOrder(
            so_id=so_id,
            status=SOStatus.CONFIRMED,
            lines=[mock_so_line],
            shipments=[],
        )
        mock_db.get.return_value = mock_so
        mock_feature_enabled.return_value = True

        reservation = MagicMock(reservation_id=uuid.uuid4())
        mock_reservation_service = MagicMock()
        mock_reservation_service.get_reservations_for_source.return_value = [
            reservation
        ]
        mock_reservation_service_class.return_value = mock_reservation_service

        SalesOrderService.cancel(
            db=mock_db,
            so_id=str(so_id),
            cancelled_by=str(uuid.uuid4()),
            reason="Customer request",
        )

        mock_reservation_service.cancel.assert_called_once()

    @patch("app.services.hooks.emit_hook_event")
    def test_cancel_emits_service_hook(self, mock_emit_hook):
        """Test cancelling SO emits sales.order.cancelled hook event."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()
        mock_so_line = MockSalesOrderLine(so_id=so_id)
        mock_so = MockSalesOrder(
            so_id=so_id,
            status=SOStatus.CONFIRMED,
            lines=[mock_so_line],
            shipments=[],
        )
        mock_db.get.return_value = mock_so

        SalesOrderService.cancel(
            db=mock_db,
            so_id=str(so_id),
            cancelled_by=str(uuid.uuid4()),
            reason="Customer request",
        )

        mock_emit_hook.assert_called_once()


class TestHold:
    """Tests for hold method."""

    def test_hold_confirmed_order(self):
        """Test putting order on hold."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()
        user_id = uuid.uuid4()

        mock_so = MockSalesOrder(so_id=so_id, status=SOStatus.CONFIRMED)
        mock_db.get.return_value = mock_so

        result = SalesOrderService.hold(
            db=mock_db,
            so_id=str(so_id),
            held_by=str(user_id),
        )

        assert result.status == SOStatus.ON_HOLD
        mock_db.flush.assert_called_once()

    def test_hold_not_found(self):
        """Test holding non-existent order."""
        mock_db = MagicMock()
        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc_info:
            SalesOrderService.hold(
                db=mock_db,
                so_id=str(uuid.uuid4()),
                held_by=str(uuid.uuid4()),
            )

        assert "not found" in str(exc_info.value)

    def test_hold_completed_order(self):
        """Test holding completed order fails."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()

        mock_so = MockSalesOrder(so_id=so_id, status=SOStatus.COMPLETED)
        mock_db.get.return_value = mock_so

        with pytest.raises(ValueError) as exc_info:
            SalesOrderService.hold(
                db=mock_db,
                so_id=str(so_id),
                held_by=str(uuid.uuid4()),
            )

        assert "Cannot hold" in str(exc_info.value)


class TestReleaseHold:
    """Tests for release_hold method."""

    def test_release_hold_to_submitted(self):
        """Test releasing hold returns to submitted status."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()
        user_id = uuid.uuid4()

        mock_so_line = MockSalesOrderLine(
            so_id=so_id,
            quantity_shipped=Decimal("0"),
        )
        mock_so = MockSalesOrder(
            so_id=so_id,
            status=SOStatus.ON_HOLD,
            lines=[mock_so_line],
            confirmed_at=None,
            approved_at=None,
        )
        mock_db.get.return_value = mock_so

        result = SalesOrderService.release_hold(
            db=mock_db,
            so_id=str(so_id),
            released_by=str(user_id),
        )

        assert result.status == SOStatus.SUBMITTED
        mock_db.flush.assert_called_once()

    def test_release_hold_to_approved(self):
        """Test releasing hold returns to approved status."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()
        user_id = uuid.uuid4()

        mock_so_line = MockSalesOrderLine(
            so_id=so_id,
            quantity_shipped=Decimal("0"),
        )
        mock_so = MockSalesOrder(
            so_id=so_id,
            status=SOStatus.ON_HOLD,
            lines=[mock_so_line],
            confirmed_at=None,
            approved_at=datetime.now(UTC),
        )
        mock_db.get.return_value = mock_so

        result = SalesOrderService.release_hold(
            db=mock_db,
            so_id=str(so_id),
            released_by=str(user_id),
        )

        assert result.status == SOStatus.APPROVED

    def test_release_hold_to_in_progress(self):
        """Test releasing hold returns to in_progress status when partially shipped."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()
        user_id = uuid.uuid4()

        mock_so_line = MockSalesOrderLine(
            so_id=so_id,
            quantity_ordered=Decimal("10"),
            quantity_shipped=Decimal("5"),  # Partially shipped
        )
        mock_so = MockSalesOrder(
            so_id=so_id,
            status=SOStatus.ON_HOLD,
            lines=[mock_so_line],
        )
        mock_db.get.return_value = mock_so

        result = SalesOrderService.release_hold(
            db=mock_db,
            so_id=str(so_id),
            released_by=str(user_id),
        )

        assert result.status == SOStatus.IN_PROGRESS

    def test_release_hold_not_found(self):
        """Test releasing hold on non-existent order."""
        mock_db = MagicMock()
        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc_info:
            SalesOrderService.release_hold(
                db=mock_db,
                so_id=str(uuid.uuid4()),
                released_by=str(uuid.uuid4()),
            )

        assert "not found" in str(exc_info.value)

    def test_release_hold_not_on_hold(self):
        """Test releasing hold on order not on hold fails."""
        mock_db = MagicMock()
        so_id = uuid.uuid4()

        mock_so = MockSalesOrder(so_id=so_id, status=SOStatus.DRAFT)
        mock_db.get.return_value = mock_so

        with pytest.raises(ValueError) as exc_info:
            SalesOrderService.release_hold(
                db=mock_db,
                so_id=str(so_id),
                released_by=str(uuid.uuid4()),
            )

        assert "not on hold" in str(exc_info.value)


class TestListOrders:
    """Tests for list_orders method."""

    def test_list_orders_basic(self):
        """Test listing orders with no filters."""
        mock_db = MagicMock()
        org_id = str(uuid.uuid4())

        mock_orders = [MockSalesOrder(), MockSalesOrder()]
        # list_orders uses db.scalars(stmt).all()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = mock_orders
        mock_db.scalars.return_value = mock_scalars

        result = SalesOrderService.list_orders(
            db=mock_db,
            organization_id=org_id,
        )

        assert len(result) == 2

    def test_list_orders_with_customer_filter(self):
        """Test listing orders with customer filter."""
        mock_db = MagicMock()
        org_id = str(uuid.uuid4())
        customer_id = str(uuid.uuid4())

        mock_orders = [MockSalesOrder()]
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = mock_orders
        mock_db.scalars.return_value = mock_scalars

        result = SalesOrderService.list_orders(
            db=mock_db,
            organization_id=org_id,
            customer_id=customer_id,
        )

        assert len(result) == 1

    def test_list_orders_with_status_filter(self):
        """Test listing orders with status filter."""
        mock_db = MagicMock()
        org_id = str(uuid.uuid4())

        mock_orders = [MockSalesOrder()]
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = mock_orders
        mock_db.scalars.return_value = mock_scalars

        result = SalesOrderService.list_orders(
            db=mock_db,
            organization_id=org_id,
            status=SOStatus.DRAFT,
        )

        assert len(result) == 1

    def test_list_orders_with_date_range(self):
        """Test listing orders with date range filter."""
        mock_db = MagicMock()
        org_id = str(uuid.uuid4())

        mock_orders = [MockSalesOrder()]
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = mock_orders
        mock_db.scalars.return_value = mock_scalars

        result = SalesOrderService.list_orders(
            db=mock_db,
            organization_id=org_id,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )

        assert len(result) == 1

    def test_list_orders_with_pagination(self):
        """Test listing orders with pagination."""
        mock_db = MagicMock()
        org_id = str(uuid.uuid4())

        mock_orders = [MockSalesOrder()]
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = mock_orders
        mock_db.scalars.return_value = mock_scalars

        result = SalesOrderService.list_orders(
            db=mock_db,
            organization_id=org_id,
            limit=10,
            offset=5,
        )

        assert len(result) == 1
