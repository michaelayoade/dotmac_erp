"""
Tests for FIFOValuationService.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.models.inventory.item import CostingMethod
from app.services.inventory.fifo_valuation import (
    FIFOInventory,
    FIFOValuationService,
    NRVCalculation,
)
from tests.ifrs.inv.conftest import (
    MockInventoryLot,
    MockInventoryValuation,
    MockItem,
)


@pytest.fixture
def service():
    """Create FIFOValuationService instance."""
    return FIFOValuationService()


@pytest.fixture
def org_id():
    """Create test organization ID."""
    return uuid4()


@pytest.fixture
def warehouse_id():
    """Create test warehouse ID shared by sample FIFO layers."""
    return uuid4()


@pytest.fixture
def sample_fifo_layers(warehouse_id):
    """Create sample FIFO layers for testing (all in one warehouse)."""
    return [
        MockInventoryLot(
            lot_number="LOT-001",
            warehouse_id=warehouse_id,
            received_date=date(2024, 1, 1),
            quantity_on_hand=Decimal("50"),
            quantity_allocated=Decimal("0"),
            quantity_available=Decimal("50"),
            unit_cost=Decimal("10.00"),
            is_active=True,
        ),
        MockInventoryLot(
            lot_number="LOT-002",
            warehouse_id=warehouse_id,
            received_date=date(2024, 2, 1),
            quantity_on_hand=Decimal("30"),
            quantity_allocated=Decimal("0"),
            quantity_available=Decimal("30"),
            unit_cost=Decimal("12.00"),
            is_active=True,
        ),
        MockInventoryLot(
            lot_number="LOT-003",
            warehouse_id=warehouse_id,
            received_date=date(2024, 3, 1),
            quantity_on_hand=Decimal("20"),
            quantity_allocated=Decimal("0"),
            quantity_available=Decimal("20"),
            unit_cost=Decimal("15.00"),
            is_active=True,
        ),
    ]


class TestAddInventoryLayer:
    """Tests for add_inventory_layer method."""

    def test_add_layer_success(self, service, mock_db, org_id):
        """Test successful layer addition."""
        item = MockItem(organization_id=org_id)
        mock_db.scalars.return_value.first.return_value = item
        mock_db.scalar.side_effect = [Decimal("0"), Decimal("0")]

        service.add_inventory_layer(
            mock_db,
            org_id,
            item.item_id,
            uuid4(),
            quantity=Decimal("100"),
            unit_cost=Decimal("10.00"),
            layer_date=date.today(),
        )

        assert mock_db.add.call_count == 2
        mock_db.flush.assert_called()

    def test_add_layer_item_not_found(self, service, mock_db, org_id):
        """Test layer addition with invalid item."""
        from fastapi import HTTPException

        mock_db.scalars.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.add_inventory_layer(
                mock_db,
                org_id,
                uuid4(),
                uuid4(),
                quantity=Decimal("100"),
                unit_cost=Decimal("10.00"),
                layer_date=date.today(),
            )

        assert exc.value.status_code == 404

    def test_add_layer_rejects_zero_quantity(self, service, mock_db, org_id):
        """F21: zero/negative quantity must be rejected."""
        from app.services.common import ValidationError

        item = MockItem(organization_id=org_id)
        mock_db.scalars.return_value.first.return_value = item

        with pytest.raises(ValidationError):
            service.add_inventory_layer(
                mock_db,
                org_id,
                item.item_id,
                uuid4(),
                quantity=Decimal("0"),
                unit_cost=Decimal("10.00"),
                layer_date=date.today(),
            )

    def test_add_layer_rejects_non_positive_unit_cost(self, service, mock_db, org_id):
        """F21: zero/negative unit cost must be rejected."""
        from app.services.common import ValidationError

        item = MockItem(organization_id=org_id)
        mock_db.scalars.return_value.first.return_value = item

        with pytest.raises(ValidationError):
            service.add_inventory_layer(
                mock_db,
                org_id,
                item.item_id,
                uuid4(),
                quantity=Decimal("100"),
                unit_cost=Decimal("0"),
                layer_date=date.today(),
            )


class TestConsumeInventoryFifo:
    """Tests for consume_inventory_fifo method."""

    def test_consume_success(
        self, service, mock_db, org_id, sample_fifo_layers, warehouse_id
    ):
        """Test successful FIFO consumption."""
        mock_db.scalars.return_value.all.return_value = sample_fifo_layers

        result = service.consume_inventory_fifo(
            mock_db, org_id, uuid4(), Decimal("60"), warehouse_id
        )

        mock_db.flush.assert_called()
        assert result.quantity_consumed == Decimal("60")
        # First 50 units at $10 + next 10 units at $12 = 500 + 120 = 620
        assert result.total_cost == Decimal("620")
        assert len(result.cost_layers_used) == 2

    def test_consume_insufficient_inventory(
        self, service, mock_db, org_id, sample_fifo_layers, warehouse_id
    ):
        """Test consumption with insufficient inventory."""
        from fastapi import HTTPException

        mock_db.scalars.return_value.all.return_value = sample_fifo_layers

        with pytest.raises(HTTPException) as exc:
            service.consume_inventory_fifo(
                mock_db,
                org_id,
                uuid4(),
                Decimal("200"),  # More than available
                warehouse_id,
            )

        assert exc.value.status_code == 400
        assert "Insufficient inventory" in exc.value.detail

    def test_consume_from_single_layer(
        self, service, mock_db, org_id, sample_fifo_layers, warehouse_id
    ):
        """Test consumption from single layer."""
        mock_db.scalars.return_value.all.return_value = sample_fifo_layers

        result = service.consume_inventory_fifo(
            mock_db, org_id, uuid4(), Decimal("30"), warehouse_id
        )

        assert result.quantity_consumed == Decimal("30")
        # 30 units at $10 = 300
        assert result.total_cost == Decimal("300")
        assert len(result.cost_layers_used) == 1

    def test_consume_only_from_specified_warehouse(
        self, service, mock_db, org_id, sample_fifo_layers, warehouse_id
    ):
        """FIFO consumption must ignore layers in other warehouses (F20)."""
        from fastapi import HTTPException

        other_warehouse_id = uuid4()
        other_layer = MockInventoryLot(
            lot_number="LOT-OTHER",
            warehouse_id=other_warehouse_id,
            received_date=date(2024, 1, 1),
            quantity_on_hand=Decimal("500"),
            quantity_allocated=Decimal("0"),
            quantity_available=Decimal("500"),
            unit_cost=Decimal("10.00"),
            is_active=True,
        )
        mock_db.scalars.return_value.all.return_value = [
            *sample_fifo_layers,
            other_layer,
        ]

        # Only 100 units live in warehouse_id; requesting 150 must fail even
        # though the other warehouse has 500 on hand.
        with pytest.raises(HTTPException) as exc:
            service.consume_inventory_fifo(
                mock_db, org_id, uuid4(), Decimal("150"), warehouse_id
            )

        assert exc.value.status_code == 400
        assert "Insufficient inventory" in exc.value.detail


class TestGetFifoInventory:
    """Tests for get_fifo_inventory method."""

    def test_get_fifo_inventory_success(
        self, service, mock_db, org_id, sample_fifo_layers
    ):
        """Test getting FIFO inventory state."""
        mock_db.scalars.return_value.all.return_value = sample_fifo_layers

        result = service.get_fifo_inventory(mock_db, org_id, uuid4())

        assert len(result.layers) == 3
        assert result.total_quantity == Decimal("100")  # 50 + 30 + 20
        # Total cost: 50*10 + 30*12 + 20*15 = 500 + 360 + 300 = 1160
        assert result.total_cost == Decimal("1160")

    def test_get_fifo_inventory_empty(self, service, mock_db, org_id):
        """Test getting empty FIFO inventory."""
        mock_db.scalars.return_value.all.return_value = []

        result = service.get_fifo_inventory(mock_db, org_id, uuid4())

        assert len(result.layers) == 0
        assert result.total_quantity == Decimal("0")
        assert result.total_cost == Decimal("0")


class TestCalculateNRV:
    """Tests for calculate_nrv method."""

    def test_calculate_nrv_positive(self, service):
        """Test NRV calculation with positive value."""
        result = service.calculate_nrv(
            estimated_selling_price=Decimal("100.00"),
            costs_to_complete=Decimal("10.00"),
            selling_costs=Decimal("5.00"),
        )

        # NRV = 100 - 10 - 5 = 85
        assert result == Decimal("85.00")

    def test_calculate_nrv_negative(self, service):
        """Test NRV calculation resulting in negative."""
        result = service.calculate_nrv(
            estimated_selling_price=Decimal("50.00"),
            costs_to_complete=Decimal("30.00"),
            selling_costs=Decimal("25.00"),
        )

        # NRV = 50 - 30 - 25 = -5
        assert result == Decimal("-5.00")

    def test_calculate_nrv_zero_costs(self, service):
        """Test NRV with zero additional costs."""
        result = service.calculate_nrv(
            estimated_selling_price=Decimal("100.00"),
            costs_to_complete=Decimal("0"),
            selling_costs=Decimal("0"),
        )

        assert result == Decimal("100.00")


class TestCalculateWriteDown:
    """Tests for calculate_write_down method."""

    def test_write_down_required(self, service, mock_db, org_id):
        """Test write-down when NRV < Cost."""
        # Mock inventory with cost > NRV
        fifo_inv = FIFOInventory(
            item_id=uuid4(),
            layers=[],
            total_quantity=Decimal("100"),
            total_cost=Decimal("1500.00"),  # $15 per unit cost
            weighted_average_cost=Decimal("15.00"),
        )

        with patch.object(
            FIFOValuationService, "get_fifo_inventory", return_value=fifo_inv
        ):
            result = service.calculate_write_down(
                mock_db,
                org_id,
                uuid4(),
                uuid4(),
                uuid4(),
                date.today(),
                estimated_selling_price=Decimal("12.00"),  # NRV per unit
                costs_to_complete=Decimal("0"),
                selling_costs=Decimal("0"),
            )

        # Cost = 15, NRV = 12, write-down per unit = 3
        # Total write-down = 3 * 100 = 300
        assert result.write_down == Decimal("300")
        assert result.carrying_amount == Decimal("1200")  # 12 * 100

    def test_no_write_down_required(self, service, mock_db, org_id):
        """Test no write-down when NRV >= Cost."""
        fifo_inv = FIFOInventory(
            item_id=uuid4(),
            layers=[],
            total_quantity=Decimal("100"),
            total_cost=Decimal("1000.00"),  # $10 per unit cost
            weighted_average_cost=Decimal("10.00"),
        )

        with patch.object(
            FIFOValuationService, "get_fifo_inventory", return_value=fifo_inv
        ):
            result = service.calculate_write_down(
                mock_db,
                org_id,
                uuid4(),
                uuid4(),
                uuid4(),
                date.today(),
                estimated_selling_price=Decimal("15.00"),  # NRV > Cost
                costs_to_complete=Decimal("0"),
                selling_costs=Decimal("0"),
            )

        assert result.write_down == Decimal("0")
        assert result.carrying_amount == Decimal("1000.00")

    def test_write_down_zero_inventory(self, service, mock_db, org_id):
        """Test write-down calculation with zero inventory."""
        fifo_inv = FIFOInventory(
            item_id=uuid4(),
            layers=[],
            total_quantity=Decimal("0"),
            total_cost=Decimal("0"),
            weighted_average_cost=Decimal("0"),
        )

        with patch.object(
            FIFOValuationService, "get_fifo_inventory", return_value=fifo_inv
        ):
            result = service.calculate_write_down(
                mock_db,
                org_id,
                uuid4(),
                uuid4(),
                uuid4(),
                date.today(),
                estimated_selling_price=Decimal("10.00"),
            )

        assert result.write_down == Decimal("0")
        assert result.carrying_amount == Decimal("0")

    def test_write_down_uses_warehouse_scoped_fifo_inventory(
        self, service, mock_db, org_id
    ):
        fifo_inv = FIFOInventory(
            item_id=uuid4(),
            layers=[],
            total_quantity=Decimal("10"),
            total_cost=Decimal("100"),
            weighted_average_cost=Decimal("10"),
        )
        item_id = uuid4()
        warehouse_id = uuid4()

        with patch.object(
            FIFOValuationService, "get_fifo_inventory", return_value=fifo_inv
        ) as mock_get_fifo_inventory:
            service.calculate_write_down(
                mock_db,
                org_id,
                item_id,
                warehouse_id,
                uuid4(),
                date.today(),
                estimated_selling_price=Decimal("15.00"),
            )

        mock_get_fifo_inventory.assert_called_once_with(
            mock_db, org_id, item_id, warehouse_id
        )


class TestCreateValuationRecord:
    """Tests for create_valuation_record method."""

    def test_create_new_valuation(self, service, mock_db, org_id):
        """Test creating a new valuation record."""
        item = MockItem(organization_id=org_id, base_uom="EACH")
        item.costing_method = CostingMethod.WEIGHTED_AVERAGE
        mock_db.scalars.return_value.first.side_effect = [item, None]

        fifo_inv = FIFOInventory(
            item_id=item.item_id,
            layers=[],
            total_quantity=Decimal("100"),
            total_cost=Decimal("1000.00"),
            weighted_average_cost=Decimal("10.00"),
        )

        nrv_calc = NRVCalculation(
            item_id=item.item_id,
            cost=Decimal("1000.00"),
            estimated_selling_price=Decimal("12.00"),
            costs_to_complete=Decimal("0"),
            selling_costs=Decimal("1.00"),
            nrv=Decimal("1100.00"),
            carrying_amount=Decimal("1000.00"),
            write_down=Decimal("0"),
        )

        with patch.object(
            FIFOValuationService, "get_fifo_inventory", return_value=fifo_inv
        ):
            service.create_valuation_record(
                mock_db,
                org_id,
                item.item_id,
                uuid4(),
                uuid4(),
                date.today(),
                nrv_calc,
            )

        mock_db.add.assert_called_once()
        mock_db.flush.assert_called()

    def test_create_valuation_item_not_found(self, service, mock_db, org_id):
        """Test creating valuation for non-existent item."""
        from fastapi import HTTPException

        mock_db.scalars.return_value.first.return_value = None

        nrv_calc = NRVCalculation(
            item_id=uuid4(),
            cost=Decimal("1000.00"),
            estimated_selling_price=Decimal("12.00"),
            costs_to_complete=Decimal("0"),
            selling_costs=Decimal("0"),
            nrv=Decimal("1200.00"),
            carrying_amount=Decimal("1000.00"),
            write_down=Decimal("0"),
        )

        with pytest.raises(HTTPException) as exc:
            service.create_valuation_record(
                mock_db,
                org_id,
                uuid4(),
                uuid4(),
                uuid4(),
                date.today(),
                nrv_calc,
            )

        assert exc.value.status_code == 404

    def test_create_valuation_uses_warehouse_scoped_fifo_inventory(
        self, service, mock_db, org_id
    ):
        item = MockItem(organization_id=org_id, base_uom="EACH")
        item.costing_method = CostingMethod.WEIGHTED_AVERAGE
        warehouse_id = uuid4()
        mock_db.scalars.return_value.first.side_effect = [item, None]

        fifo_inv = FIFOInventory(
            item_id=item.item_id,
            layers=[],
            total_quantity=Decimal("100"),
            total_cost=Decimal("1000.00"),
            weighted_average_cost=Decimal("10.00"),
        )

        nrv_calc = NRVCalculation(
            item_id=item.item_id,
            cost=Decimal("1000.00"),
            estimated_selling_price=Decimal("12.00"),
            costs_to_complete=Decimal("0"),
            selling_costs=Decimal("1.00"),
            nrv=Decimal("1100.00"),
            carrying_amount=Decimal("1000.00"),
            write_down=Decimal("0"),
        )

        with patch.object(
            FIFOValuationService, "get_fifo_inventory", return_value=fifo_inv
        ) as mock_get_fifo_inventory:
            service.create_valuation_record(
                mock_db,
                org_id,
                item.item_id,
                warehouse_id,
                uuid4(),
                date.today(),
                nrv_calc,
            )

        mock_get_fifo_inventory.assert_called_once_with(
            mock_db, org_id, item.item_id, warehouse_id
        )


class TestGetValuationSummary:
    """Tests for get_valuation_summary method."""

    def test_get_summary_success(self, service, mock_db, org_id):
        """Test getting valuation summary."""
        valuations = [
            MockInventoryValuation(
                total_cost=Decimal("1000.00"),
                carrying_amount=Decimal("900.00"),
                write_down_amount=Decimal("100.00"),
            ),
            MockInventoryValuation(
                total_cost=Decimal("500.00"),
                carrying_amount=Decimal("500.00"),
                write_down_amount=Decimal("0"),
            ),
        ]
        mock_db.scalars.return_value.all.return_value = valuations

        result = service.get_valuation_summary(mock_db, org_id, uuid4())

        assert result["item_count"] == 2
        assert result["total_cost"] == str(Decimal("1500.00"))
        assert result["total_write_down"] == str(Decimal("100.00"))

    def test_get_summary_empty(self, service, mock_db, org_id):
        """Test getting summary with no valuations."""
        mock_db.scalars.return_value.all.return_value = []

        result = service.get_valuation_summary(mock_db, org_id, uuid4())

        assert result["item_count"] == 0
        assert result["total_cost"] == str(Decimal("0"))
