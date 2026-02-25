"""
Tests for Inventory API endpoints.

These tests mock the service layer to test API routing and serialization.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.api import inventory as inv_api
from tests.api.ifrs.conftest import (
    MockInventoryItem,
    MockInventoryTransaction,
    MockLot,
)


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    return MagicMock()


class TestInventoryItemsAPI:
    """Tests for inventory item endpoints."""

    def test_create_item_success(self, mock_db, mock_auth_dict, org_id):
        """Test successful item creation."""
        mock_item = MockInventoryItem(organization_id=org_id)
        category_id = uuid.uuid4()

        with patch("app.api.inventory.item_service.create_item") as mock_create:
            mock_create.return_value = mock_item

            payload = inv_api.InventoryItemCreate(
                item_code="ITEM-001",
                item_name="Test Item",
                unit_of_measure="EACH",
                costing_method="WEIGHTED_AVERAGE",
            )
            result = inv_api.create_inventory_item(
                payload,
                organization_id=org_id,
                category_id=category_id,
                currency_code="USD",
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.item_code == "ITEM-001"

    def test_get_item_success(self, mock_db, mock_auth_dict, org_id):
        """Test getting an item."""
        mock_item = MockInventoryItem(organization_id=org_id)

        with patch("app.api.inventory.item_service.get") as mock_get:
            mock_get.return_value = mock_item

            result = inv_api.get_inventory_item(
                mock_item.item_id,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.item_code == "ITEM-001"

    def test_list_items(self, mock_db, mock_auth_dict, org_id):
        """Test listing items."""
        mock_items = [MockInventoryItem(organization_id=org_id) for _ in range(5)]

        with patch("app.api.inventory.item_service.list") as mock_list:
            mock_list.return_value = mock_items

            result = inv_api.list_inventory_items(
                organization_id=org_id,
                auth=mock_auth_dict,
                db=mock_db,
                limit=50,
                offset=0,
            )

        assert result.count == 5
        assert len(result.items) == 5

    def test_list_items_with_filters(self, mock_db, mock_auth_dict, org_id):
        """Test listing items with filters."""
        mock_items = [MockInventoryItem(organization_id=org_id, is_active=True)]

        with patch("app.api.inventory.item_service.list") as mock_list:
            mock_list.return_value = mock_items

            result = inv_api.list_inventory_items(
                organization_id=org_id,
                is_active=True,
                search="ITEM",
                auth=mock_auth_dict,
                db=mock_db,
                limit=50,
                offset=0,
            )

        assert result.count == 1


class TestInventoryTransactionsAPI:
    """Tests for inventory transaction endpoints."""

    def test_create_transaction(self, mock_db, mock_auth_dict, org_id, user_id):
        """Test creating a transaction."""
        mock_txn = MockInventoryTransaction(organization_id=org_id)
        fiscal_period_id = uuid.uuid4()

        with patch(
            "app.api.inventory.inventory_transaction_service.create_transaction"
        ) as mock_create:
            mock_create.return_value = mock_txn

            payload = inv_api.TransactionCreate(
                item_id=uuid.uuid4(),
                warehouse_id=uuid.uuid4(),
                transaction_type="RECEIPT",
                transaction_date=date.today(),
                quantity=Decimal("100.00"),
                unit_cost=Decimal("10.00"),
            )
            result = inv_api.create_inventory_transaction(
                payload,
                organization_id=org_id,
                created_by_user_id=user_id,
                fiscal_period_id=fiscal_period_id,
                uom="EACH",
                currency_code="USD",
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result == mock_txn

    def test_get_transaction(self, mock_db, mock_auth_dict, org_id):
        """Test getting a transaction."""
        mock_txn = MockInventoryTransaction(organization_id=org_id)

        with patch("app.api.inventory.inventory_transaction_service.get") as mock_get:
            mock_get.return_value = mock_txn

            result = inv_api.get_inventory_transaction(
                mock_txn.transaction_id,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.transaction_id == mock_txn.transaction_id

    def test_list_transactions(self, mock_db, mock_auth_dict, org_id):
        """Test listing transactions."""
        mock_txns = [MockInventoryTransaction(organization_id=org_id) for _ in range(5)]

        with patch("app.api.inventory.inventory_transaction_service.list") as mock_list:
            mock_list.return_value = mock_txns

            result = inv_api.list_inventory_transactions(
                organization_id=org_id,
                auth=mock_auth_dict,
                db=mock_db,
                limit=50,
                offset=0,
            )

        assert result.count == 5
        assert len(result.items) == 5

    def test_list_transactions_with_filters(self, mock_db, mock_auth_dict, org_id):
        """Test listing transactions with filters."""
        mock_txns = [
            MockInventoryTransaction(organization_id=org_id, transaction_type="RECEIPT")
        ]

        with patch("app.api.inventory.inventory_transaction_service.list") as mock_list:
            mock_list.return_value = mock_txns

            result = inv_api.list_inventory_transactions(
                organization_id=org_id,
                transaction_type="RECEIPT",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                auth=mock_auth_dict,
                db=mock_db,
                limit=50,
                offset=0,
            )

        assert result.count == 1

    def test_post_transaction(self, mock_db, mock_auth_dict, org_id, user_id):
        """Test posting a transaction to GL."""
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.journal_entry_id = uuid.uuid4()
        mock_result.entry_number = "JE-INV-0001"
        mock_result.message = "Posted successfully"

        with patch(
            "app.api.inventory.inv_posting_adapter.post_transaction"
        ) as mock_post:
            mock_post.return_value = mock_result

            result = inv_api.post_inventory_transaction(
                transaction_id=uuid.uuid4(),
                posting_date=date.today(),
                organization_id=org_id,
                posted_by_user_id=user_id,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.success is True


class TestFIFOValuationAPI:
    """Tests for FIFO valuation endpoints."""

    def test_add_fifo_layer(self, mock_db, mock_auth_dict, org_id):
        """Test adding a FIFO layer."""
        mock_result = MagicMock()

        with patch(
            "app.api.inventory.fifo_valuation_service.add_inventory_layer"
        ) as mock_add:
            mock_add.return_value = mock_result

            payload = inv_api.AddLayerCreate(
                item_id=uuid.uuid4(),
                warehouse_id=uuid.uuid4(),
                quantity=Decimal("100.00"),
                unit_cost=Decimal("10.00"),
                layer_date=date.today(),
            )
            result = inv_api.add_fifo_layer(
                payload,
                organization_id=org_id,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result == mock_result

    def test_consume_fifo(self, mock_db, mock_auth_dict, org_id):
        """Test FIFO consumption."""
        mock_result = MagicMock()
        mock_result.quantity_consumed = Decimal("50")
        mock_result.total_cost = Decimal("500")
        mock_result.cost_layers_used = [
            {"layer_date": str(date.today()), "quantity": 50, "unit_cost": 10}
        ]
        mock_result.remaining_quantity = Decimal("50")

        with patch(
            "app.api.inventory.fifo_valuation_service.consume_inventory_fifo"
        ) as mock_consume:
            mock_consume.return_value = mock_result

            result = inv_api.consume_fifo(
                item_id=uuid.uuid4(),
                quantity=Decimal("50"),
                organization_id=org_id,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.remaining_quantity == Decimal("50")

    def test_get_fifo_inventory(self, mock_db, mock_auth_dict, org_id):
        """Test getting FIFO inventory state."""
        mock_result = MagicMock()
        mock_result.item_id = uuid.uuid4()
        mock_result.layers = []
        mock_result.total_quantity = Decimal("100")
        mock_result.total_cost = Decimal("1000")
        mock_result.weighted_average_cost = Decimal("10")

        with patch(
            "app.api.inventory.fifo_valuation_service.get_fifo_inventory"
        ) as mock_get:
            mock_get.return_value = mock_result

            result = inv_api.get_fifo_inventory(
                item_id=uuid.uuid4(),
                organization_id=org_id,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.total_quantity == Decimal("100")

    def test_calculate_nrv_write_down(self, mock_db, mock_auth_dict, org_id):
        """Test NRV write-down calculation."""
        mock_result = MagicMock()
        mock_result.item_id = uuid.uuid4()
        mock_result.cost = Decimal("1000")
        mock_result.estimated_selling_price = Decimal("800")
        mock_result.costs_to_complete = Decimal("50")
        mock_result.selling_costs = Decimal("50")
        mock_result.nrv = Decimal("700")
        mock_result.carrying_amount = Decimal("700")
        mock_result.write_down = Decimal("300")

        with patch(
            "app.api.inventory.fifo_valuation_service.calculate_write_down"
        ) as mock_calc:
            mock_calc.return_value = mock_result

            result = inv_api.calculate_nrv_write_down(
                item_id=uuid.uuid4(),
                warehouse_id=uuid.uuid4(),
                fiscal_period_id=uuid.uuid4(),
                valuation_date=date.today(),
                estimated_selling_price=Decimal("800"),
                costs_to_complete=Decimal("50"),
                selling_costs=Decimal("50"),
                organization_id=org_id,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.write_down == Decimal("300")

    def test_get_valuation_summary(self, mock_db, mock_auth_dict, org_id):
        """Test getting valuation summary."""
        mock_result = {
            "item_count": 10,
            "total_cost": "10000.00",
            "total_write_down": "500.00",
        }

        with patch(
            "app.api.inventory.fifo_valuation_service.get_valuation_summary"
        ) as mock_get:
            mock_get.return_value = mock_result

            result = inv_api.get_fifo_valuation_summary(
                organization_id=org_id,
                fiscal_period_id=uuid.uuid4(),
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result["item_count"] == 10

    def test_get_inventory_valuation_reconciliation(
        self, mock_db, mock_auth_dict, org_id
    ):
        """Test inventory valuation reconciliation snapshot endpoint."""
        mock_result = MagicMock()
        mock_result.fiscal_period_id = uuid.uuid4()
        mock_result.inventory_total = Decimal("1250.00")
        mock_result.gl_total = Decimal("1000.00")
        mock_result.difference = Decimal("250.00")
        mock_result.is_balanced = False

        with patch(
            "app.api.inventory.ValuationReconciliationService.reconcile"
        ) as mock_reconcile:
            mock_reconcile.return_value = mock_result

            result = inv_api.get_inventory_valuation_reconciliation(
                organization_id=org_id,
                fiscal_period_id=None,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.fiscal_period_id == mock_result.fiscal_period_id
        assert result.inventory_total == Decimal("1250.00")
        assert result.gl_total == Decimal("1000.00")
        assert result.difference == Decimal("250.00")
        assert result.is_balanced is False


class TestLotTrackingAPI:
    """Tests for lot tracking endpoints."""

    def test_create_lot(self, mock_db, mock_auth_dict, org_id):
        """Test creating a lot."""
        mock_lot = MockLot()

        with patch("app.api.inventory.lot_serial_service.create_lot") as mock_create:
            mock_create.return_value = mock_lot

            payload = inv_api.LotCreate(
                item_id=uuid.uuid4(),
                lot_number="LOT-001",
                received_date=date.today(),
                unit_cost=Decimal("10.00"),
                initial_quantity=Decimal("100.00"),
            )
            result = inv_api.create_lot(
                payload,
                organization_id=org_id,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.lot_number == "LOT-001"

    def test_get_lot(self, mock_db, mock_auth_dict):
        """Test getting a lot."""
        mock_lot = MockLot()

        with patch("app.api.inventory.lot_serial_service.get") as mock_get:
            mock_get.return_value = mock_lot

            result = inv_api.get_lot(
                mock_lot.lot_id,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.lot_id == mock_lot.lot_id

    def test_list_lots(self, mock_db, mock_auth_dict, org_id):
        """Test listing lots."""
        mock_lots = [MockLot() for _ in range(5)]

        with patch("app.api.inventory.lot_serial_service.list") as mock_list:
            mock_list.return_value = mock_lots

            result = inv_api.list_lots(
                organization_id=org_id,
                auth=mock_auth_dict,
                db=mock_db,
                limit=50,
                offset=0,
            )

        assert result.count == 5
        assert len(result.items) == 5

    def test_list_lots_with_filters(self, mock_db, mock_auth_dict, org_id):
        """Test listing lots with filters."""
        mock_lots = [MockLot(is_quarantined=False)]

        with patch("app.api.inventory.lot_serial_service.list") as mock_list:
            mock_list.return_value = mock_lots

            result = inv_api.list_lots(
                organization_id=org_id,
                is_quarantined=False,
                has_expiry=True,
                auth=mock_auth_dict,
                db=mock_db,
                limit=50,
                offset=0,
            )

        assert result.count == 1

    def test_allocate_from_lot(self, mock_db, mock_auth_dict):
        """Test allocating from a lot."""
        mock_lot = MockLot(quantity_available=Decimal("50"))

        with patch(
            "app.api.inventory.lot_serial_service.allocate_from_lot"
        ) as mock_allocate:
            mock_allocate.return_value = mock_lot

            result = inv_api.allocate_from_lot(
                mock_lot.lot_id,
                quantity=Decimal("50"),
                reference="Test",
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.quantity_available == Decimal("50")

    def test_deallocate_from_lot(self, mock_db, mock_auth_dict):
        """Test deallocating from a lot."""
        mock_lot = MockLot(quantity_available=Decimal("100"))

        with patch(
            "app.api.inventory.lot_serial_service.deallocate_from_lot"
        ) as mock_deallocate:
            mock_deallocate.return_value = mock_lot

            result = inv_api.deallocate_from_lot(
                mock_lot.lot_id,
                quantity=Decimal("50"),
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.quantity_available == Decimal("100")

    def test_consume_from_lot(self, mock_db, mock_auth_dict):
        """Test consuming from a lot."""
        mock_lot = MockLot(quantity_on_hand=Decimal("50"))

        with patch(
            "app.api.inventory.lot_serial_service.consume_from_lot"
        ) as mock_consume:
            mock_consume.return_value = mock_lot

            result = inv_api.consume_from_lot(
                mock_lot.lot_id,
                quantity=Decimal("50"),
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.quantity_on_hand == Decimal("50")

    def test_quarantine_lot(self, mock_db, mock_auth_dict):
        """Test quarantining a lot."""
        mock_lot = MockLot(is_quarantined=True)

        with patch(
            "app.api.inventory.lot_serial_service.quarantine_lot"
        ) as mock_quarantine:
            mock_quarantine.return_value = mock_lot

            result = inv_api.quarantine_lot(
                mock_lot.lot_id,
                reason="Quality issue",
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.is_quarantined is True

    def test_release_quarantine(self, mock_db, mock_auth_dict):
        """Test releasing lot from quarantine."""
        mock_lot = MockLot(is_quarantined=False)

        with patch(
            "app.api.inventory.lot_serial_service.release_quarantine"
        ) as mock_release:
            mock_release.return_value = mock_lot

            result = inv_api.release_quarantine(
                mock_lot.lot_id,
                qc_status="PASSED",
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.is_quarantined is False

    def test_get_lot_traceability(self, mock_db, mock_auth_dict):
        """Test getting lot traceability."""
        mock_trace = MagicMock()
        mock_trace.lot_id = uuid.uuid4()
        mock_trace.lot_number = "LOT-001"
        mock_trace.item_id = uuid.uuid4()
        mock_trace.item_code = "ITEM-001"
        mock_trace.supplier_lot = "SUPP-LOT-001"
        mock_trace.received_date = date.today()
        mock_trace.expiry_date = None
        mock_trace.total_received = Decimal("100")
        mock_trace.total_remaining = Decimal("75")
        mock_trace.total_consumed = Decimal("25")

        with patch("app.api.inventory.lot_serial_service.get_traceability") as mock_get:
            mock_get.return_value = mock_trace

            result = inv_api.get_lot_traceability(
                uuid.uuid4(),
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.total_remaining == Decimal("75")

    def test_get_expiring_lots(self, mock_db, mock_auth_dict, org_id):
        """Test getting expiring lots."""
        mock_lots = [MockLot(expiry_date=date(2024, 2, 15)) for _ in range(3)]

        with patch(
            "app.api.inventory.lot_serial_service.get_expiring_lots"
        ) as mock_get:
            mock_get.return_value = mock_lots

            result = inv_api.get_expiring_lots(
                organization_id=org_id,
                days_ahead=30,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.count == 3

    def test_get_expired_lots(self, mock_db, mock_auth_dict, org_id):
        """Test getting expired lots."""
        mock_lots = [MockLot(expiry_date=date(2024, 1, 1)) for _ in range(2)]

        with patch("app.api.inventory.lot_serial_service.get_expired_lots") as mock_get:
            mock_get.return_value = mock_lots

            result = inv_api.get_expired_lots(
                organization_id=org_id,
                auth=mock_auth_dict,
                db=mock_db,
            )

        assert result.count == 2
