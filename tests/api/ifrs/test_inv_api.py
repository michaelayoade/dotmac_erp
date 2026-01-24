"""
Tests for Inventory API endpoints.

These tests mock the service layer to test API routing and serialization.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from app.api.finance.inv import router, get_db
from app.api.deps import require_tenant_auth
from tests.api.ifrs.conftest import (
    MockInventoryItem,
    MockInventoryTransaction,
    MockLot,
)


@pytest.fixture
def app():
    """Create a test FastAPI app."""
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    return MagicMock()


@pytest.fixture
def client(app, mock_db, mock_auth_dict, auth_headers):
    """Create test client with mocked dependencies."""
    app.dependency_overrides[get_db] = lambda: mock_db
    def _require_tenant_auth_override(authorization: str | None = Header(default=None)):
        if not authorization:
            raise HTTPException(status_code=401, detail="Unauthorized")
        return mock_auth_dict

    app.dependency_overrides[require_tenant_auth] = _require_tenant_auth_override
    test_client = TestClient(app)
    test_client.headers.update(auth_headers)
    return test_client


class TestInventoryItemsAPI:
    """Tests for inventory item endpoints."""

    def test_create_item_success(self, client, org_id):
        """Test successful item creation."""
        mock_item = MockInventoryItem(organization_id=org_id)
        category_id = uuid.uuid4()

        with patch("app.api.ifrs.inv.item_service.create_item") as mock_create:
            mock_create.return_value = mock_item

            response = client.post(
                f"/inv/items?category_id={category_id}",
                json={
                    "item_code": "ITEM-001",
                    "item_name": "Test Item",
                    "unit_of_measure": "EACH",
                    "costing_method": "WEIGHTED_AVERAGE",
                },
            )

        assert response.status_code == 201

    def test_get_item_success(self, client, org_id):
        """Test getting an item."""
        mock_item = MockInventoryItem(organization_id=org_id)

        with patch("app.api.ifrs.inv.item_service.get") as mock_get:
            mock_get.return_value = mock_item

            response = client.get(f"/inv/items/{mock_item.item_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["item_code"] == "ITEM-001"

    def test_list_items(self, client, org_id):
        """Test listing items."""
        mock_items = [MockInventoryItem(organization_id=org_id) for _ in range(5)]

        with patch("app.api.ifrs.inv.item_service.list") as mock_list:
            mock_list.return_value = mock_items

            response = client.get(f"/inv/items")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 5

    def test_list_items_with_filters(self, client, org_id):
        """Test listing items with filters."""
        mock_items = [MockInventoryItem(organization_id=org_id, is_active=True)]

        with patch("app.api.ifrs.inv.item_service.list") as mock_list:
            mock_list.return_value = mock_items

            response = client.get(
                f"/inv/items?costing_method=FIFO&is_active=true"
            )

        assert response.status_code == 200


class TestInventoryTransactionsAPI:
    """Tests for inventory transaction endpoints."""

    def test_create_transaction(self, client, org_id, user_id):
        """Test creating a transaction."""
        mock_txn = MockInventoryTransaction(organization_id=org_id)
        fiscal_period_id = uuid.uuid4()

        with patch("app.api.ifrs.inv.inventory_transaction_service.create_transaction") as mock_create:
            mock_create.return_value = mock_txn

            response = client.post(
                f"/inv/transactions?created_by_user_id={user_id}&fiscal_period_id={fiscal_period_id}",
                json={
                    "item_id": str(uuid.uuid4()),
                    "warehouse_id": str(uuid.uuid4()),
                    "transaction_type": "RECEIPT",
                    "transaction_date": str(date.today()),
                    "quantity": "100.00",
                    "unit_cost": "10.00",
                },
            )

        assert response.status_code == 201

    def test_get_transaction(self, client, org_id):
        """Test getting a transaction."""
        mock_txn = MockInventoryTransaction(organization_id=org_id)

        with patch("app.api.ifrs.inv.inventory_transaction_service.get") as mock_get:
            mock_get.return_value = mock_txn

            response = client.get(f"/inv/transactions/{mock_txn.transaction_id}")

        assert response.status_code == 200

    def test_list_transactions(self, client, org_id):
        """Test listing transactions."""
        mock_txns = [MockInventoryTransaction(organization_id=org_id) for _ in range(5)]

        with patch("app.api.ifrs.inv.inventory_transaction_service.list") as mock_list:
            mock_list.return_value = mock_txns

            response = client.get(f"/inv/transactions")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 5

    def test_list_transactions_with_filters(self, client, org_id):
        """Test listing transactions with filters."""
        mock_txns = [MockInventoryTransaction(organization_id=org_id, transaction_type="RECEIPT")]

        with patch("app.api.ifrs.inv.inventory_transaction_service.list") as mock_list:
            mock_list.return_value = mock_txns

            response = client.get(
                f"/inv/transactions"
                f"&transaction_type=RECEIPT&start_date=2024-01-01&end_date=2024-01-31"
            )

        assert response.status_code == 200

    def test_post_transaction(self, client, org_id, user_id):
        """Test posting a transaction to GL."""
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.journal_entry_id = uuid.uuid4()
        mock_result.entry_number = "JE-INV-0001"
        mock_result.message = "Posted successfully"

        with patch("app.api.ifrs.inv.inv_posting_adapter.post_transaction") as mock_post:
            mock_post.return_value = mock_result

            response = client.post(
                f"/inv/transactions/{uuid.uuid4()}/post"
                f"?posting_date={date.today()}&posted_by_user_id={user_id}"
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


class TestFIFOValuationAPI:
    """Tests for FIFO valuation endpoints."""

    def test_add_fifo_layer(self, client, org_id):
        """Test adding a FIFO layer."""
        mock_result = MagicMock()

        with patch("app.api.ifrs.inv.fifo_valuation_service.add_inventory_layer") as mock_add:
            mock_add.return_value = mock_result

            response = client.post(
                f"/inv/fifo/add-layer",
                json={
                    "item_id": str(uuid.uuid4()),
                    "warehouse_id": str(uuid.uuid4()),
                    "quantity": "100.00",
                    "unit_cost": "10.00",
                    "layer_date": str(date.today()),
                },
            )

        assert response.status_code == 201

    def test_consume_fifo(self, client, org_id):
        """Test FIFO consumption."""
        mock_result = MagicMock()
        mock_result.quantity_consumed = Decimal("50")
        mock_result.total_cost = Decimal("500")
        mock_result.cost_layers_used = [{"layer_date": str(date.today()), "quantity": 50, "unit_cost": 10}]
        mock_result.remaining_quantity = Decimal("50")

        with patch("app.api.ifrs.inv.fifo_valuation_service.consume_inventory_fifo") as mock_consume:
            mock_consume.return_value = mock_result

            response = client.post(
                f"/inv/fifo/consume?item_id={uuid.uuid4()}&quantity=50"
            )

        assert response.status_code == 200

    def test_get_fifo_inventory(self, client, org_id):
        """Test getting FIFO inventory state."""
        mock_result = MagicMock()
        mock_result.item_id = uuid.uuid4()
        mock_result.layers = []
        mock_result.total_quantity = Decimal("100")
        mock_result.total_cost = Decimal("1000")
        mock_result.weighted_average_cost = Decimal("10")

        with patch("app.api.ifrs.inv.fifo_valuation_service.get_fifo_inventory") as mock_get:
            mock_get.return_value = mock_result

            response = client.get(
                f"/inv/fifo/{uuid.uuid4()}"
            )

        assert response.status_code == 200

    def test_calculate_nrv_write_down(self, client, org_id):
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

        with patch("app.api.ifrs.inv.fifo_valuation_service.calculate_write_down") as mock_calc:
            mock_calc.return_value = mock_result

            response = client.post(
                f"/inv/fifo/calculate-nrv"
                f"?item_id={uuid.uuid4()}&warehouse_id={uuid.uuid4()}"
                f"&fiscal_period_id={uuid.uuid4()}&valuation_date={date.today()}"
                f"&estimated_selling_price=800"
            )

        assert response.status_code == 200

    def test_get_valuation_summary(self, client, org_id):
        """Test getting valuation summary."""
        mock_result = {
            "item_count": 10,
            "total_cost": "10000.00",
            "total_write_down": "500.00",
        }

        with patch("app.api.ifrs.inv.fifo_valuation_service.get_valuation_summary") as mock_get:
            mock_get.return_value = mock_result

            response = client.get(
                f"/inv/fifo/valuation-summary?fiscal_period_id={uuid.uuid4()}"
            )

        assert response.status_code == 200


class TestLotTrackingAPI:
    """Tests for lot tracking endpoints."""

    def test_create_lot(self, client, org_id):
        """Test creating a lot."""
        mock_lot = MockLot()

        with patch("app.api.ifrs.inv.lot_serial_service.create_lot") as mock_create:
            mock_create.return_value = mock_lot

            response = client.post(
                f"/inv/lots",
                json={
                    "item_id": str(uuid.uuid4()),
                    "lot_number": "LOT-001",
                    "received_date": str(date.today()),
                    "unit_cost": "10.00",
                    "initial_quantity": "100.00",
                },
            )

        assert response.status_code == 201

    def test_get_lot(self, client):
        """Test getting a lot."""
        mock_lot = MockLot()

        with patch("app.api.ifrs.inv.lot_serial_service.get") as mock_get:
            mock_get.return_value = mock_lot

            response = client.get(f"/inv/lots/{mock_lot.lot_id}")

        assert response.status_code == 200

    def test_list_lots(self, client, org_id):
        """Test listing lots."""
        mock_lots = [MockLot() for _ in range(5)]

        with patch("app.api.ifrs.inv.lot_serial_service.list") as mock_list:
            mock_list.return_value = mock_lots

            response = client.get(f"/inv/lots")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 5

    def test_list_lots_with_filters(self, client, org_id):
        """Test listing lots with filters."""
        mock_lots = [MockLot(is_quarantined=False)]

        with patch("app.api.ifrs.inv.lot_serial_service.list") as mock_list:
            mock_list.return_value = mock_lots

            response = client.get(
                f"/inv/lots?is_quarantined=false&has_expiry=true"
            )

        assert response.status_code == 200

    def test_allocate_from_lot(self, client):
        """Test allocating from a lot."""
        mock_lot = MockLot(quantity_available=Decimal("50"))

        with patch("app.api.ifrs.inv.lot_serial_service.allocate_from_lot") as mock_allocate:
            mock_allocate.return_value = mock_lot

            response = client.post(
                f"/inv/lots/{mock_lot.lot_id}/allocate?quantity=50"
            )

        assert response.status_code == 200

    def test_deallocate_from_lot(self, client):
        """Test deallocating from a lot."""
        mock_lot = MockLot(quantity_available=Decimal("100"))

        with patch("app.api.ifrs.inv.lot_serial_service.deallocate_from_lot") as mock_deallocate:
            mock_deallocate.return_value = mock_lot

            response = client.post(
                f"/inv/lots/{mock_lot.lot_id}/deallocate?quantity=50"
            )

        assert response.status_code == 200

    def test_consume_from_lot(self, client):
        """Test consuming from a lot."""
        mock_lot = MockLot(quantity_on_hand=Decimal("50"))

        with patch("app.api.ifrs.inv.lot_serial_service.consume_from_lot") as mock_consume:
            mock_consume.return_value = mock_lot

            response = client.post(
                f"/inv/lots/{mock_lot.lot_id}/consume?quantity=50"
            )

        assert response.status_code == 200

    def test_quarantine_lot(self, client):
        """Test quarantining a lot."""
        mock_lot = MockLot(is_quarantined=True)

        with patch("app.api.ifrs.inv.lot_serial_service.quarantine_lot") as mock_quarantine:
            mock_quarantine.return_value = mock_lot

            response = client.post(
                f"/inv/lots/{mock_lot.lot_id}/quarantine?reason=Quality+issue"
            )

        assert response.status_code == 200

    def test_release_quarantine(self, client):
        """Test releasing lot from quarantine."""
        mock_lot = MockLot(is_quarantined=False)

        with patch("app.api.ifrs.inv.lot_serial_service.release_quarantine") as mock_release:
            mock_release.return_value = mock_lot

            response = client.post(
                f"/inv/lots/{mock_lot.lot_id}/release-quarantine?qc_status=PASSED"
            )

        assert response.status_code == 200

    def test_get_lot_traceability(self, client):
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

        with patch("app.api.ifrs.inv.lot_serial_service.get_traceability") as mock_get:
            mock_get.return_value = mock_trace

            response = client.get(f"/inv/lots/{uuid.uuid4()}/traceability")

        assert response.status_code == 200

    def test_get_expiring_lots(self, client, org_id):
        """Test getting expiring lots."""
        mock_lots = [MockLot(expiry_date=date(2024, 2, 15)) for _ in range(3)]

        with patch("app.api.ifrs.inv.lot_serial_service.get_expiring_lots") as mock_get:
            mock_get.return_value = mock_lots

            response = client.get(
                f"/inv/lots/expiring?days_ahead=30"
            )

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 3

    def test_get_expired_lots(self, client, org_id):
        """Test getting expired lots."""
        mock_lots = [MockLot(expiry_date=date(2024, 1, 1)) for _ in range(2)]

        with patch("app.api.ifrs.inv.lot_serial_service.get_expired_lots") as mock_get:
            mock_get.return_value = mock_lots

            response = client.get(f"/inv/lots/expired")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
