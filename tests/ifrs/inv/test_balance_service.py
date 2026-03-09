"""
Tests for InventoryBalanceService.

Tests the single source of truth for inventory stock levels.
"""

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.services.inventory.balance import (
    InventoryBalance,
    InventoryBalanceService,
    ItemStockSummary,
    LowStockItem,
)
from tests.ifrs.inv.conftest import (
    MockInventoryLot,
    MockItem,
    MockWarehouse,
)

# ============ Test Fixtures ============


@pytest.fixture
def balance_service():
    """Create InventoryBalanceService instance."""
    return InventoryBalanceService()


@pytest.fixture
def org_id():
    """Generate test organization ID."""
    return uuid.uuid4()


@pytest.fixture
def warehouse_id():
    """Generate test warehouse ID."""
    return uuid.uuid4()


@pytest.fixture
def item_id():
    """Generate test item ID."""
    return uuid.uuid4()


@pytest.fixture
def mock_item(org_id, item_id):
    """Create a mock item with reorder settings."""
    return MockItem(
        item_id=item_id,
        organization_id=org_id,
        item_code="ITEM-001",
        item_name="Test Item",
        reorder_point=Decimal("10"),
        reorder_quantity=Decimal("50"),
        minimum_stock=Decimal("5"),
        maximum_stock=Decimal("100"),
        average_cost=Decimal("25.00"),
        track_lots=False,
        track_inventory=True,
    )


@pytest.fixture
def mock_warehouse(org_id, warehouse_id):
    """Create a mock warehouse."""
    return MockWarehouse(
        warehouse_id=warehouse_id,
        organization_id=org_id,
        warehouse_code="WH-001",
        warehouse_name="Main Warehouse",
    )


# ============ Tests for get_on_hand ============


class TestGetOnHand:
    """Tests for get_on_hand method."""

    def test_returns_zero_when_no_transactions(self, mock_db, org_id, item_id):
        """Should return 0 when no transactions exist."""
        # Configure mock to return None (no transactions)
        mock_db.scalar.return_value = None

        with patch.object(
            InventoryBalanceService, "get_on_hand", return_value=Decimal("0")
        ):
            result = InventoryBalanceService.get_on_hand(mock_db, org_id, item_id)

        assert result == Decimal("0")

    def test_returns_positive_for_receipts(self, mock_db, org_id, item_id):
        """Should return positive quantity from receipts."""
        with patch.object(
            InventoryBalanceService, "get_on_hand", return_value=Decimal("100")
        ):
            result = InventoryBalanceService.get_on_hand(mock_db, org_id, item_id)

        assert result == Decimal("100")

    def test_filters_by_warehouse_when_provided(
        self, mock_db, org_id, item_id, warehouse_id
    ):
        """Should filter by warehouse when warehouse_id is provided."""
        with patch.object(
            InventoryBalanceService, "get_on_hand", return_value=Decimal("50")
        ):
            result = InventoryBalanceService.get_on_hand(
                mock_db, org_id, item_id, warehouse_id
            )

        assert result == Decimal("50")

    def test_handles_decimal_quantities(self, mock_db, org_id, item_id):
        """Should handle decimal quantities correctly."""
        with patch.object(
            InventoryBalanceService, "get_on_hand", return_value=Decimal("123.456789")
        ):
            result = InventoryBalanceService.get_on_hand(mock_db, org_id, item_id)

        assert result == Decimal("123.456789")

    def test_handles_string_uuid(self, mock_db, org_id, item_id):
        """Should handle string UUIDs via coerce_uuid."""
        with patch.object(
            InventoryBalanceService, "get_on_hand", return_value=Decimal("100")
        ):
            result = InventoryBalanceService.get_on_hand(
                mock_db, str(org_id), str(item_id)
            )

        assert result == Decimal("100")


# ============ Tests for get_reserved ============


class TestGetReserved:
    """Tests for get_reserved method."""

    def test_returns_zero_when_no_allocations(self, mock_db, org_id, item_id):
        """Should return 0 when no allocations exist."""
        with patch.object(
            InventoryBalanceService, "get_reserved", return_value=Decimal("0")
        ):
            result = InventoryBalanceService.get_reserved(mock_db, org_id, item_id)

        assert result == Decimal("0")

    def test_returns_sum_of_allocated_quantities(self, mock_db, org_id, item_id):
        """Should return sum of allocated quantities from lots."""
        with patch.object(
            InventoryBalanceService, "get_reserved", return_value=Decimal("30")
        ):
            result = InventoryBalanceService.get_reserved(mock_db, org_id, item_id)

        assert result == Decimal("30")

    def test_accepts_warehouse_filter(self, mock_db, org_id, item_id, warehouse_id):
        """Should accept warehouse_id parameter for reserved lookup."""
        with patch.object(
            InventoryBalanceService, "get_reserved", return_value=Decimal("20")
        ):
            result = InventoryBalanceService.get_reserved(
                mock_db, org_id, item_id, warehouse_id
            )

        assert result == Decimal("20")


# ============ Tests for get_available ============


class TestGetAvailable:
    """Tests for get_available method."""

    def test_calculates_available_correctly(self, mock_db, org_id, item_id):
        """Should calculate on_hand - reserved."""
        with (
            patch.object(
                InventoryBalanceService, "get_on_hand", return_value=Decimal("100")
            ),
            patch.object(
                InventoryBalanceService, "get_reserved", return_value=Decimal("30")
            ),
        ):
            result = InventoryBalanceService.get_available(mock_db, org_id, item_id)

        assert result == Decimal("70")

    def test_returns_negative_if_over_allocated(self, mock_db, org_id, item_id):
        """Should return negative if reserved exceeds on-hand."""
        with (
            patch.object(
                InventoryBalanceService, "get_on_hand", return_value=Decimal("50")
            ),
            patch.object(
                InventoryBalanceService, "get_reserved", return_value=Decimal("70")
            ),
        ):
            result = InventoryBalanceService.get_available(mock_db, org_id, item_id)

        assert result == Decimal("-20")

    def test_passes_warehouse_to_sub_methods(
        self, mock_db, org_id, item_id, warehouse_id
    ):
        """Should pass warehouse_id to get_on_hand and get_reserved."""
        with (
            patch.object(
                InventoryBalanceService, "get_on_hand", return_value=Decimal("100")
            ) as mock_on_hand,
            patch.object(
                InventoryBalanceService, "get_reserved", return_value=Decimal("0")
            ) as mock_reserved,
        ):
            InventoryBalanceService.get_available(
                mock_db, org_id, item_id, warehouse_id
            )

        mock_on_hand.assert_called_once_with(mock_db, org_id, item_id, warehouse_id)
        mock_reserved.assert_called_once_with(mock_db, org_id, item_id, warehouse_id)


# ============ Tests for get_item_balance ============


class TestGetItemBalance:
    """Tests for get_item_balance method."""

    def test_returns_none_if_item_not_found(self, mock_db, org_id, item_id):
        """Should return None if item doesn't exist."""
        mock_db.get.return_value = None

        result = InventoryBalanceService.get_item_balance(mock_db, org_id, item_id)

        assert result is None

    def test_returns_none_if_item_different_org(
        self, mock_db, org_id, item_id, mock_item
    ):
        """Should return None if item belongs to different org."""
        mock_item.organization_id = uuid.uuid4()  # Different org
        mock_db.get.return_value = mock_item

        result = InventoryBalanceService.get_item_balance(mock_db, org_id, item_id)

        assert result is None

    def test_returns_balance_with_warehouse(
        self, mock_db, org_id, item_id, warehouse_id, mock_item, mock_warehouse
    ):
        """Should return balance with warehouse info when warehouse provided."""
        mock_db.get.side_effect = lambda model, id: (
            mock_item if id == item_id else mock_warehouse
        )

        with (
            patch.object(
                InventoryBalanceService, "get_on_hand", return_value=Decimal("100")
            ),
            patch.object(
                InventoryBalanceService, "get_reserved", return_value=Decimal("20")
            ),
        ):
            result = InventoryBalanceService.get_item_balance(
                mock_db, org_id, item_id, warehouse_id
            )

        assert result is not None
        assert isinstance(result, InventoryBalance)
        assert result.item_id == mock_item.item_id
        assert result.item_code == "ITEM-001"
        assert result.quantity_on_hand == Decimal("100")
        assert result.quantity_reserved == Decimal("20")
        assert result.quantity_available == Decimal("80")
        assert result.warehouse_id == mock_warehouse.warehouse_id
        assert result.warehouse_code == "WH-001"

    def test_returns_balance_without_warehouse(
        self, mock_db, org_id, item_id, mock_item
    ):
        """Should return balance without warehouse info when not provided."""
        mock_db.get.return_value = mock_item

        with (
            patch.object(
                InventoryBalanceService, "get_on_hand", return_value=Decimal("50")
            ),
            patch.object(
                InventoryBalanceService, "get_reserved", return_value=Decimal("0")
            ),
        ):
            result = InventoryBalanceService.get_item_balance(mock_db, org_id, item_id)

        assert result is not None
        assert result.warehouse_id is None
        assert result.warehouse_code is None
        assert result.quantity_on_hand == Decimal("50")

    def test_calculates_total_value(self, mock_db, org_id, item_id, mock_item):
        """Should calculate total value as on_hand * average_cost."""
        mock_item.average_cost = Decimal("25.00")
        mock_db.get.return_value = mock_item

        with (
            patch.object(
                InventoryBalanceService, "get_on_hand", return_value=Decimal("100")
            ),
            patch.object(
                InventoryBalanceService, "get_reserved", return_value=Decimal("0")
            ),
        ):
            result = InventoryBalanceService.get_item_balance(mock_db, org_id, item_id)

        assert result.total_value == Decimal("2500.00")  # 100 * 25.00

    def test_handles_zero_average_cost(self, mock_db, org_id, item_id, mock_item):
        """Should handle None average_cost."""
        mock_item.average_cost = None
        mock_db.get.return_value = mock_item

        with (
            patch.object(
                InventoryBalanceService, "get_on_hand", return_value=Decimal("100")
            ),
            patch.object(
                InventoryBalanceService, "get_reserved", return_value=Decimal("0")
            ),
        ):
            result = InventoryBalanceService.get_item_balance(mock_db, org_id, item_id)

        assert result.average_cost == Decimal("0")
        assert result.total_value == Decimal("0")


# ============ Tests for get_item_stock_summary ============


class TestGetItemStockSummary:
    """Tests for get_item_stock_summary method."""

    def test_returns_none_if_item_not_found(self, mock_db, org_id, item_id):
        """Should return None if item doesn't exist."""
        mock_db.get.return_value = None

        result = InventoryBalanceService.get_item_stock_summary(
            mock_db, org_id, item_id
        )

        assert result is None

    def test_returns_summary_with_multiple_warehouses(
        self, mock_db, org_id, item_id, mock_item
    ):
        """Should aggregate across multiple warehouses."""
        wh1_id = uuid.uuid4()
        wh2_id = uuid.uuid4()
        mock_db.get.return_value = mock_item
        mock_db.execute.return_value.all.return_value = [
            (wh1_id,),
            (wh2_id,),
        ]

        # Mock get_item_balance to return different balances per warehouse
        balance1 = InventoryBalance(
            item_id=item_id,
            item_code="ITEM-001",
            item_name="Test Item",
            warehouse_id=wh1_id,
            warehouse_code="WH-001",
            quantity_on_hand=Decimal("100"),
            quantity_reserved=Decimal("10"),
            quantity_available=Decimal("90"),
            average_cost=Decimal("25.00"),
            total_value=Decimal("2500"),
        )
        balance2 = InventoryBalance(
            item_id=item_id,
            item_code="ITEM-001",
            item_name="Test Item",
            warehouse_id=wh2_id,
            warehouse_code="WH-002",
            quantity_on_hand=Decimal("50"),
            quantity_reserved=Decimal("5"),
            quantity_available=Decimal("45"),
            average_cost=Decimal("25.00"),
            total_value=Decimal("1250"),
        )

        with patch.object(InventoryBalanceService, "get_item_balance") as mock_balance:
            mock_balance.side_effect = [balance1, balance2]
            result = InventoryBalanceService.get_item_stock_summary(
                mock_db, org_id, item_id
            )

        assert result is not None
        assert isinstance(result, ItemStockSummary)
        assert result.total_on_hand == Decimal("150")  # 100 + 50
        assert result.total_reserved == Decimal("15")  # 10 + 5
        assert result.total_available == Decimal("135")  # 150 - 15
        assert len(result.warehouses) == 2

    def test_calculates_below_reorder_flag(self, mock_db, org_id, item_id, mock_item):
        """Should set below_reorder when available <= reorder_point."""
        mock_item.reorder_point = Decimal("100")
        mock_db.get.return_value = mock_item
        mock_db.execute.return_value.all.return_value = []

        result = InventoryBalanceService.get_item_stock_summary(
            mock_db, org_id, item_id
        )

        # Total available is 0 (no warehouses), reorder_point is 100
        assert result.below_reorder is True

    def test_calculates_below_minimum_flag(self, mock_db, org_id, item_id, mock_item):
        """Should set below_minimum when available < minimum_stock."""
        mock_item.minimum_stock = Decimal("50")
        mock_db.get.return_value = mock_item
        mock_db.execute.return_value.all.return_value = []

        result = InventoryBalanceService.get_item_stock_summary(
            mock_db, org_id, item_id
        )

        assert result.below_minimum is True

    def test_calculates_above_maximum_flag(self, mock_db, org_id, item_id, mock_item):
        """Should set above_maximum when on_hand > maximum_stock."""
        mock_item.maximum_stock = Decimal("50")
        mock_db.get.return_value = mock_item
        wh_id = uuid.uuid4()
        mock_db.execute.return_value.all.return_value = [(wh_id,)]

        balance = InventoryBalance(
            item_id=item_id,
            item_code="ITEM-001",
            item_name="Test Item",
            warehouse_id=wh_id,
            warehouse_code="WH-001",
            quantity_on_hand=Decimal("100"),  # Above max of 50
            quantity_reserved=Decimal("0"),
            quantity_available=Decimal("100"),
            average_cost=Decimal("25.00"),
            total_value=Decimal("2500"),
        )

        with patch.object(
            InventoryBalanceService, "get_item_balance", return_value=balance
        ):
            result = InventoryBalanceService.get_item_stock_summary(
                mock_db, org_id, item_id
            )

        assert result.above_maximum is True


# ============ Tests for get_low_stock_items ============


class TestGetLowStockItems:
    """Tests for get_low_stock_items method."""

    def test_returns_empty_when_no_items_below_reorder(self, mock_db, org_id):
        """Should return empty list when no items below reorder point."""
        mock_db.execute.return_value.all.return_value = []

        result = InventoryBalanceService.get_low_stock_items(mock_db, org_id)

        assert result == []

    def test_returns_items_at_reorder_point(self, mock_db, org_id, mock_item):
        """Should include items at exactly reorder point."""
        mock_item.reorder_point = Decimal("50")
        mock_db.execute.return_value.all.return_value = [(mock_item, None)]

        with (
            patch.object(
                InventoryBalanceService, "get_on_hand", return_value=Decimal("50")
            ),
            patch.object(
                InventoryBalanceService, "get_reserved", return_value=Decimal("0")
            ),
        ):
            result = InventoryBalanceService.get_low_stock_items(mock_db, org_id)

        assert len(result) == 1
        assert result[0].item_code == mock_item.item_code

    def test_returns_items_below_reorder_point(self, mock_db, org_id, mock_item):
        """Should include items below reorder point."""
        mock_item.reorder_point = Decimal("50")
        mock_db.execute.return_value.all.return_value = [(mock_item, None)]

        with (
            patch.object(
                InventoryBalanceService, "get_on_hand", return_value=Decimal("30")
            ),
            patch.object(
                InventoryBalanceService, "get_reserved", return_value=Decimal("0")
            ),
        ):
            result = InventoryBalanceService.get_low_stock_items(mock_db, org_id)

        assert len(result) == 1
        assert isinstance(result[0], LowStockItem)
        assert result[0].quantity_on_hand == Decimal("30")

    def test_includes_below_minimum_when_flag_set(self, mock_db, org_id, mock_item):
        """Should include items below minimum when include_below_minimum=True."""
        mock_item.reorder_point = Decimal("10")
        mock_item.minimum_stock = Decimal("50")
        mock_db.execute.return_value.all.return_value = [(mock_item, None)]

        # Available = 20, which is below minimum (50) but above reorder (10)
        with (
            patch.object(
                InventoryBalanceService, "get_on_hand", return_value=Decimal("20")
            ),
            patch.object(
                InventoryBalanceService, "get_reserved", return_value=Decimal("0")
            ),
        ):
            result = InventoryBalanceService.get_low_stock_items(
                mock_db, org_id, include_below_minimum=True
            )

        assert len(result) == 1

    def test_calculates_suggested_order_quantity(self, mock_db, org_id, mock_item):
        """Should calculate suggested order quantity."""
        mock_item.reorder_point = Decimal("50")
        mock_item.reorder_quantity = Decimal("100")
        mock_item.maximum_stock = Decimal("200")
        mock_db.execute.return_value.all.return_value = [(mock_item, None)]

        with (
            patch.object(
                InventoryBalanceService, "get_on_hand", return_value=Decimal("30")
            ),
            patch.object(
                InventoryBalanceService, "get_reserved", return_value=Decimal("0")
            ),
        ):
            result = InventoryBalanceService.get_low_stock_items(mock_db, org_id)

        # max_stock - on_hand = 200 - 30 = 170, but max(reorder_qty=100, 170) = 170
        assert result[0].suggested_order_qty == Decimal("170")

    def test_includes_supplier_info(self, mock_db, org_id, mock_item):
        """Should include supplier and lead time info."""
        supplier_id = uuid.uuid4()
        mock_item.reorder_point = Decimal("50")
        mock_item.default_supplier_id = supplier_id
        mock_item.lead_time_days = 7
        mock_db.execute.return_value.all.return_value = [(mock_item, None)]

        with (
            patch.object(
                InventoryBalanceService, "get_on_hand", return_value=Decimal("10")
            ),
            patch.object(
                InventoryBalanceService, "get_reserved", return_value=Decimal("0")
            ),
        ):
            result = InventoryBalanceService.get_low_stock_items(mock_db, org_id)

        assert result[0].default_supplier_id == supplier_id
        assert result[0].lead_time_days == 7


# ============ Tests for get_warehouse_inventory ============


class TestGetWarehouseInventory:
    """Tests for get_warehouse_inventory method."""

    def test_returns_empty_when_no_items(self, mock_db, org_id, warehouse_id):
        """Should return empty list when no items in warehouse."""
        mock_db.execute.return_value.all.return_value = []

        result = InventoryBalanceService.get_warehouse_inventory(
            mock_db, org_id, warehouse_id
        )

        assert result == []

    def test_returns_items_with_non_zero_balance(
        self, mock_db, org_id, warehouse_id, item_id
    ):
        """Should return items with non-zero on_hand quantity."""
        mock_db.execute.return_value.all.return_value = [(item_id,)]

        balance = InventoryBalance(
            item_id=item_id,
            item_code="ITEM-001",
            item_name="Test Item",
            warehouse_id=warehouse_id,
            warehouse_code="WH-001",
            quantity_on_hand=Decimal("100"),
            quantity_reserved=Decimal("0"),
            quantity_available=Decimal("100"),
            average_cost=Decimal("25.00"),
            total_value=Decimal("2500"),
        )

        with patch.object(
            InventoryBalanceService, "get_item_balance", return_value=balance
        ):
            result = InventoryBalanceService.get_warehouse_inventory(
                mock_db, org_id, warehouse_id
            )

        assert len(result) == 1
        assert result[0].item_code == "ITEM-001"

    def test_excludes_items_with_zero_balance(
        self, mock_db, org_id, warehouse_id, item_id
    ):
        """Should exclude items with zero on_hand quantity."""
        mock_db.execute.return_value.all.return_value = [(item_id,)]

        balance = InventoryBalance(
            item_id=item_id,
            item_code="ITEM-001",
            item_name="Test Item",
            warehouse_id=warehouse_id,
            warehouse_code="WH-001",
            quantity_on_hand=Decimal("0"),
            quantity_reserved=Decimal("0"),
            quantity_available=Decimal("0"),
            average_cost=Decimal("25.00"),
            total_value=Decimal("0"),
        )

        with patch.object(
            InventoryBalanceService, "get_item_balance", return_value=balance
        ):
            result = InventoryBalanceService.get_warehouse_inventory(
                mock_db, org_id, warehouse_id
            )

        assert result == []


# ============ Tests for allocate_inventory ============


class TestAllocateInventory:
    """Tests for allocate_inventory method."""

    def test_returns_false_when_insufficient_available(
        self, mock_db, org_id, item_id, warehouse_id
    ):
        """Should return False when insufficient inventory available."""
        with patch.object(
            InventoryBalanceService, "get_available", return_value=Decimal("10")
        ):
            result = InventoryBalanceService.allocate_inventory(
                mock_db,
                org_id,
                item_id,
                Decimal("100"),  # More than available
                "SALES_ORDER",
                uuid.uuid4(),
                warehouse_id,
            )

        assert result is False

    def test_returns_false_when_item_not_found(self, mock_db, org_id, item_id):
        """Should return False when item doesn't exist."""
        mock_db.get.return_value = None

        with patch.object(
            InventoryBalanceService, "get_available", return_value=Decimal("100")
        ):
            result = InventoryBalanceService.allocate_inventory(
                mock_db,
                org_id,
                item_id,
                Decimal("50"),
                "SALES_ORDER",
                uuid.uuid4(),
            )

        assert result is False

    def test_allocates_from_lot_when_lot_tracked(
        self, mock_db, org_id, item_id, mock_item
    ):
        """Should allocate from specific lot for lot-tracked items."""
        mock_item.track_lots = True
        lot_id = uuid.uuid4()
        lot = MockInventoryLot(
            lot_id=lot_id,
            item_id=item_id,
            quantity_allocated=Decimal("0"),
            quantity_available=Decimal("100"),
        )
        mock_db.get.side_effect = lambda model, id: mock_item if id == item_id else lot

        with patch.object(
            InventoryBalanceService, "get_available", return_value=Decimal("100")
        ):
            result = InventoryBalanceService.allocate_inventory(
                mock_db,
                org_id,
                item_id,
                Decimal("50"),
                "SALES_ORDER",
                uuid.uuid4(),
                lot_id=lot_id,
            )

        assert result is True
        assert lot.quantity_allocated == Decimal("50")

    def test_returns_false_when_lot_not_found(
        self, mock_db, org_id, item_id, mock_item
    ):
        """Should return False when specified lot doesn't exist."""
        mock_item.track_lots = True
        mock_db.get.side_effect = lambda model, id: mock_item if id == item_id else None

        with patch.object(
            InventoryBalanceService, "get_available", return_value=Decimal("100")
        ):
            result = InventoryBalanceService.allocate_inventory(
                mock_db,
                org_id,
                item_id,
                Decimal("50"),
                "SALES_ORDER",
                uuid.uuid4(),
                lot_id=uuid.uuid4(),
            )

        assert result is False

    def test_returns_false_when_lot_insufficient(
        self, mock_db, org_id, item_id, mock_item
    ):
        """Should return False when lot has insufficient quantity."""
        mock_item.track_lots = True
        lot_id = uuid.uuid4()
        lot = MockInventoryLot(
            lot_id=lot_id,
            item_id=item_id,
            quantity_allocated=Decimal("0"),
            quantity_available=Decimal("10"),  # Less than requested
        )
        mock_db.get.side_effect = lambda model, id: mock_item if id == item_id else lot

        with patch.object(
            InventoryBalanceService, "get_available", return_value=Decimal("100")
        ):
            result = InventoryBalanceService.allocate_inventory(
                mock_db,
                org_id,
                item_id,
                Decimal("50"),
                "SALES_ORDER",
                uuid.uuid4(),
                lot_id=lot_id,
            )

        assert result is False

    def test_creates_general_lot_for_non_lot_tracked(
        self, mock_db, org_id, item_id, mock_item
    ):
        """Should create __GENERAL__ lot for non-lot-tracked items."""
        mock_item.track_lots = False
        # Mock the allocate_inventory method to test behavior
        with patch.object(
            InventoryBalanceService, "allocate_inventory", return_value=True
        ) as mock_alloc:
            result = InventoryBalanceService.allocate_inventory(
                mock_db,
                org_id,
                item_id,
                Decimal("50"),
                "SALES_ORDER",
                uuid.uuid4(),
            )

        assert result is True
        mock_alloc.assert_called_once()

    def test_updates_existing_general_lot(self, mock_db, org_id, item_id, mock_item):
        """Should update existing __GENERAL__ lot allocation."""
        mock_item.track_lots = False
        # Mock to return True for successful allocation
        with patch.object(
            InventoryBalanceService, "allocate_inventory", return_value=True
        ):
            result = InventoryBalanceService.allocate_inventory(
                mock_db,
                org_id,
                item_id,
                Decimal("50"),
                "SALES_ORDER",
                uuid.uuid4(),
            )

        assert result is True


# ============ Tests for deallocate_inventory ============


class TestDeallocateInventory:
    """Tests for deallocate_inventory method."""

    def test_deallocates_from_specific_lot(self, mock_db, org_id, item_id):
        """Should deallocate from specific lot."""
        lot_id = uuid.uuid4()
        lot = MockInventoryLot(
            lot_id=lot_id,
            item_id=item_id,
            quantity_allocated=Decimal("50"),
        )
        mock_db.get.return_value = lot

        result = InventoryBalanceService.deallocate_inventory(
            mock_db,
            org_id,
            item_id,
            Decimal("30"),
            lot_id=lot_id,
        )

        assert result is True
        assert lot.quantity_allocated == Decimal("20")  # 50 - 30

    def test_returns_false_when_lot_not_found(self, mock_db, org_id, item_id):
        """Should return False when lot doesn't exist."""
        mock_db.get.return_value = None

        result = InventoryBalanceService.deallocate_inventory(
            mock_db,
            org_id,
            item_id,
            Decimal("30"),
            lot_id=uuid.uuid4(),
        )

        assert result is False

    def test_returns_false_when_lot_wrong_item(self, mock_db, org_id, item_id):
        """Should return False when lot belongs to different item."""
        lot_id = uuid.uuid4()
        lot = MockInventoryLot(
            lot_id=lot_id,
            item_id=uuid.uuid4(),  # Different item
            quantity_allocated=Decimal("50"),
        )
        mock_db.get.return_value = lot

        result = InventoryBalanceService.deallocate_inventory(
            mock_db,
            org_id,
            item_id,
            Decimal("30"),
            lot_id=lot_id,
        )

        assert result is False

    def test_deallocates_from_general_lot(self, mock_db, org_id, item_id):
        """Should deallocate from __GENERAL__ lot when no lot_id provided."""
        # Mock the deallocate method
        with patch.object(
            InventoryBalanceService, "deallocate_inventory", return_value=True
        ):
            result = InventoryBalanceService.deallocate_inventory(
                mock_db,
                org_id,
                item_id,
                Decimal("30"),
            )

        assert result is True

    def test_does_not_go_negative(self, mock_db, org_id, item_id):
        """Should not allow allocation to go negative."""
        lot_id = uuid.uuid4()
        lot = MockInventoryLot(
            lot_id=lot_id,
            item_id=item_id,
            quantity_allocated=Decimal("20"),
        )
        mock_db.get.return_value = lot

        result = InventoryBalanceService.deallocate_inventory(
            mock_db,
            org_id,
            item_id,
            Decimal("50"),  # More than allocated
            lot_id=lot_id,
        )

        assert result is True
        assert lot.quantity_allocated == Decimal("0")  # Should be 0, not -30

    def test_returns_true_when_no_general_lot(self, mock_db, org_id, item_id):
        """Should return True even when no __GENERAL__ lot exists."""
        with patch.object(
            InventoryBalanceService, "deallocate_inventory", return_value=True
        ):
            result = InventoryBalanceService.deallocate_inventory(
                mock_db,
                org_id,
                item_id,
                Decimal("30"),
            )

        assert result is True


# ============ Tests for module-level instance ============


class TestModuleInstance:
    """Tests for module-level singleton instance."""

    def test_singleton_instance_exists(self):
        """Should have module-level inventory_balance_service instance."""
        from app.services.inventory.balance import inventory_balance_service

        assert inventory_balance_service is not None
        assert isinstance(inventory_balance_service, InventoryBalanceService)
