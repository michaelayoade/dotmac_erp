"""
Tests for InventoryTransactionService.

Tests inventory receipts, issues, adjustments, transfers, and costing methods.
"""

import uuid
from datetime import datetime, timezone, date
from decimal import Decimal
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from fastapi import HTTPException

from app.services.finance.inv.transaction import (
    InventoryTransactionService,
    TransactionInput,
    CostingResult,
)
from app.models.finance.inv.item import CostingMethod
from app.models.finance.inv.inventory_transaction import TransactionType


class MockItem:
    """Mock Item model."""

    def __init__(
        self,
        item_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        item_code: str = "TEST-001",
        costing_method: CostingMethod = CostingMethod.WEIGHTED_AVERAGE,
        average_cost: Decimal = Decimal("10.00"),
        standard_cost: Decimal = None,
        last_purchase_cost: Decimal = None,
        track_lots: bool = False,
        category_id: uuid.UUID = None,
        inventory_account_id: uuid.UUID = None,
        cogs_account_id: uuid.UUID = None,
    ):
        self.item_id = item_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.item_code = item_code
        self.costing_method = costing_method
        self.average_cost = average_cost
        self.standard_cost = standard_cost
        self.last_purchase_cost = last_purchase_cost
        self.track_lots = track_lots
        self.category_id = category_id or uuid.uuid4()
        self.inventory_account_id = inventory_account_id
        self.cogs_account_id = cogs_account_id


class MockWarehouse:
    """Mock Warehouse model."""

    def __init__(
        self,
        warehouse_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        warehouse_code: str = "WH-001",
        is_receiving: bool = True,
        is_shipping: bool = True,
    ):
        self.warehouse_id = warehouse_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.warehouse_code = warehouse_code
        self.is_receiving = is_receiving
        self.is_shipping = is_shipping


class MockInventoryLot:
    """Mock InventoryLot model."""

    def __init__(
        self,
        lot_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        item_id: uuid.UUID = None,
        warehouse_id: uuid.UUID = None,
        lot_number: str = "LOT-001",
        quantity_on_hand: Decimal = Decimal("100.00"),
        quantity_allocated: Decimal = Decimal("0"),
        quantity_available: Decimal = None,
        unit_cost: Decimal = Decimal("10.00"),
        received_date: date = None,
        is_active: bool = True,
        is_quarantined: bool = False,
        initial_quantity: Decimal = None,
    ):
        self.lot_id = lot_id or uuid.uuid4()
        self.organization_id = organization_id
        self.item_id = item_id or uuid.uuid4()
        self.warehouse_id = warehouse_id
        self.lot_number = lot_number
        self.quantity_on_hand = quantity_on_hand
        self.quantity_allocated = quantity_allocated
        self.quantity_available = quantity_available if quantity_available is not None else quantity_on_hand
        self.unit_cost = unit_cost
        self.received_date = received_date or date.today()
        self.is_active = is_active
        self.is_quarantined = is_quarantined
        self.initial_quantity = initial_quantity or quantity_on_hand


class MockInventoryTransaction:
    """Mock InventoryTransaction model."""

    def __init__(
        self,
        transaction_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        transaction_type: TransactionType = TransactionType.RECEIPT,
        transaction_date: datetime = None,
        item_id: uuid.UUID = None,
        warehouse_id: uuid.UUID = None,
        quantity: Decimal = Decimal("10.00"),
        unit_cost: Decimal = Decimal("10.00"),
        total_cost: Decimal = None,
        currency_code: str = "USD",
    ):
        self.transaction_id = transaction_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.transaction_type = transaction_type
        self.transaction_date = transaction_date or datetime.now(timezone.utc)
        self.item_id = item_id or uuid.uuid4()
        self.warehouse_id = warehouse_id or uuid.uuid4()
        self.quantity = quantity
        self.unit_cost = unit_cost
        self.total_cost = total_cost or (quantity * unit_cost)
        self.currency_code = currency_code


def create_transaction_input(
    transaction_type: TransactionType = TransactionType.RECEIPT,
    item_id: uuid.UUID = None,
    warehouse_id: uuid.UUID = None,
    quantity: Decimal = Decimal("10.00"),
    unit_cost: Decimal = Decimal("10.00"),
    **kwargs
) -> TransactionInput:
    """Helper to create TransactionInput."""
    return TransactionInput(
        transaction_type=transaction_type,
        transaction_date=kwargs.get("transaction_date", datetime.now(timezone.utc)),
        fiscal_period_id=kwargs.get("fiscal_period_id", uuid.uuid4()),
        item_id=item_id or uuid.uuid4(),
        warehouse_id=warehouse_id or uuid.uuid4(),
        quantity=quantity,
        unit_cost=unit_cost,
        uom=kwargs.get("uom", "EA"),
        currency_code=kwargs.get("currency_code", "USD"),
        location_id=kwargs.get("location_id"),
        lot_id=kwargs.get("lot_id"),
        to_warehouse_id=kwargs.get("to_warehouse_id"),
        to_location_id=kwargs.get("to_location_id"),
        source_document_type=kwargs.get("source_document_type"),
        source_document_id=kwargs.get("source_document_id"),
        source_document_line_id=kwargs.get("source_document_line_id"),
        reference=kwargs.get("reference"),
        reason_code=kwargs.get("reason_code"),
    )


class TestCalculateWeightedAverageCost:
    """Tests for calculate_weighted_average_cost method."""

    @patch("app.services.ifrs.inv.transaction.func")
    def test_calculate_with_existing_inventory(self, mock_func):
        """Test weighted average calculation with existing inventory."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        item_id = uuid.uuid4()
        wh_id = uuid.uuid4()

        # Mock existing quantity of 100 at $10
        mock_db.query.return_value.filter.return_value.scalar.return_value = Decimal("100")

        mock_item = MockItem(item_id=item_id, average_cost=Decimal("10.00"))
        mock_db.get.return_value = mock_item

        # Receive 50 at $12
        result = InventoryTransactionService.calculate_weighted_average_cost(
            db=mock_db,
            organization_id=org_id,
            item_id=item_id,
            warehouse_id=wh_id,
            new_quantity=Decimal("50"),
            new_unit_cost=Decimal("12.00"),
        )

        # (100 * 10 + 50 * 12) / 150 = 1600 / 150 = 10.666666...
        assert result == Decimal("10.666667")

    @patch("app.services.ifrs.inv.transaction.func")
    def test_calculate_with_zero_existing_inventory(self, mock_func):
        """Test weighted average calculation with no existing inventory."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        item_id = uuid.uuid4()
        wh_id = uuid.uuid4()

        # No existing inventory
        mock_db.query.return_value.filter.return_value.scalar.return_value = None

        mock_item = MockItem(item_id=item_id, average_cost=Decimal("0"))
        mock_db.get.return_value = mock_item

        # Receive 50 at $12
        result = InventoryTransactionService.calculate_weighted_average_cost(
            db=mock_db,
            organization_id=org_id,
            item_id=item_id,
            warehouse_id=wh_id,
            new_quantity=Decimal("50"),
            new_unit_cost=Decimal("12.00"),
        )

        # (0 * 0 + 50 * 12) / 50 = 12.00
        assert result == Decimal("12.000000")

    @patch("app.services.ifrs.inv.transaction.func")
    def test_calculate_returns_new_cost_when_total_quantity_zero_or_negative(self, mock_func):
        """Test when total quantity is zero or negative returns new unit cost."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        item_id = uuid.uuid4()
        wh_id = uuid.uuid4()

        # Negative existing (shouldn't happen normally)
        mock_db.query.return_value.filter.return_value.scalar.return_value = Decimal("-50")

        mock_item = MockItem(item_id=item_id, average_cost=Decimal("10.00"))
        mock_db.get.return_value = mock_item

        result = InventoryTransactionService.calculate_weighted_average_cost(
            db=mock_db,
            organization_id=org_id,
            item_id=item_id,
            warehouse_id=wh_id,
            new_quantity=Decimal("50"),
            new_unit_cost=Decimal("15.00"),
        )

        # Should return new cost when total <= 0
        assert result == Decimal("15.00")


class TestGetCurrentBalance:
    """Tests for get_current_balance method."""

    @patch("app.services.ifrs.inv.transaction.func")
    def test_get_balance_with_transactions(self, mock_func):
        """Test getting balance with various transactions."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        item_id = uuid.uuid4()
        wh_id = uuid.uuid4()

        mock_db.query.return_value.filter.return_value.scalar.return_value = Decimal("150")

        result = InventoryTransactionService.get_current_balance(
            db=mock_db,
            organization_id=org_id,
            item_id=item_id,
            warehouse_id=wh_id,
        )

        assert result == Decimal("150")

    @patch("app.services.ifrs.inv.transaction.func")
    def test_get_balance_returns_zero_when_no_transactions(self, mock_func):
        """Test getting balance when no transactions exist."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        item_id = uuid.uuid4()
        wh_id = uuid.uuid4()

        mock_db.query.return_value.filter.return_value.scalar.return_value = None

        result = InventoryTransactionService.get_current_balance(
            db=mock_db,
            organization_id=org_id,
            item_id=item_id,
            warehouse_id=wh_id,
        )

        assert result == Decimal("0")


class TestCreateReceipt:
    """Tests for create_receipt method."""

    def test_create_receipt_success(self):
        """Test successful receipt creation."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        item_id = uuid.uuid4()
        wh_id = uuid.uuid4()

        mock_item = MockItem(
            item_id=item_id,
            organization_id=org_id,
            costing_method=CostingMethod.WEIGHTED_AVERAGE,
        )
        mock_warehouse = MockWarehouse(
            warehouse_id=wh_id,
            organization_id=org_id,
            is_receiving=True,
        )

        def mock_get(model_class, id_val):
            from app.models.finance.inv.item import Item
            from app.models.finance.inv.warehouse import Warehouse

            if model_class == Item or str(model_class) == "<class 'app.models.ifrs.inv.item.Item'>":
                return mock_item
            elif model_class == Warehouse or str(model_class) == "<class 'app.models.ifrs.inv.warehouse.Warehouse'>":
                return mock_warehouse
            return None

        mock_db.get.side_effect = mock_get
        mock_db.query.return_value.filter.return_value.scalar.return_value = Decimal("100")

        input_data = create_transaction_input(
            transaction_type=TransactionType.RECEIPT,
            item_id=item_id,
            warehouse_id=wh_id,
            quantity=Decimal("50"),
            unit_cost=Decimal("12.00"),
        )

        with patch.object(
            InventoryTransactionService,
            'get_current_balance',
            return_value=Decimal("100")
        ):
            with patch.object(
                InventoryTransactionService,
                'calculate_weighted_average_cost',
                return_value=Decimal("10.666667")
            ):
                result = InventoryTransactionService.create_receipt(
                    db=mock_db,
                    organization_id=org_id,
                    input=input_data,
                    created_by_user_id=user_id,
                )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_create_receipt_item_not_found(self):
        """Test receipt fails when item not found."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        item_id = uuid.uuid4()
        wh_id = uuid.uuid4()

        mock_db.get.return_value = None

        input_data = create_transaction_input(
            transaction_type=TransactionType.RECEIPT,
            item_id=item_id,
            warehouse_id=wh_id,
        )

        with pytest.raises(HTTPException) as exc_info:
            InventoryTransactionService.create_receipt(
                db=mock_db,
                organization_id=org_id,
                input=input_data,
                created_by_user_id=user_id,
            )

        assert exc_info.value.status_code == 404
        assert "Item not found" in exc_info.value.detail

    def test_create_receipt_item_wrong_organization(self):
        """Test receipt fails when item belongs to different organization."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        other_org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        item_id = uuid.uuid4()
        wh_id = uuid.uuid4()

        mock_item = MockItem(
            item_id=item_id,
            organization_id=other_org_id,  # Different org
        )
        mock_db.get.return_value = mock_item

        input_data = create_transaction_input(
            transaction_type=TransactionType.RECEIPT,
            item_id=item_id,
            warehouse_id=wh_id,
        )

        with pytest.raises(HTTPException) as exc_info:
            InventoryTransactionService.create_receipt(
                db=mock_db,
                organization_id=org_id,
                input=input_data,
                created_by_user_id=user_id,
            )

        assert exc_info.value.status_code == 404
        assert "Item not found" in exc_info.value.detail

    def test_create_receipt_warehouse_not_found(self):
        """Test receipt fails when warehouse not found."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        item_id = uuid.uuid4()
        wh_id = uuid.uuid4()

        mock_item = MockItem(item_id=item_id, organization_id=org_id)

        def mock_get(model_class, id_val):
            from app.models.finance.inv.item import Item

            if model_class == Item or str(model_class) == "<class 'app.models.ifrs.inv.item.Item'>":
                return mock_item
            return None

        mock_db.get.side_effect = mock_get

        input_data = create_transaction_input(
            transaction_type=TransactionType.RECEIPT,
            item_id=item_id,
            warehouse_id=wh_id,
        )

        with pytest.raises(HTTPException) as exc_info:
            InventoryTransactionService.create_receipt(
                db=mock_db,
                organization_id=org_id,
                input=input_data,
                created_by_user_id=user_id,
            )

        assert exc_info.value.status_code == 404
        assert "Warehouse not found" in exc_info.value.detail

    def test_create_receipt_warehouse_not_receiving(self):
        """Test receipt fails when warehouse not configured for receiving."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        item_id = uuid.uuid4()
        wh_id = uuid.uuid4()

        mock_item = MockItem(item_id=item_id, organization_id=org_id)
        mock_warehouse = MockWarehouse(
            warehouse_id=wh_id,
            organization_id=org_id,
            is_receiving=False,  # Not a receiving warehouse
        )

        def mock_get(model_class, id_val):
            from app.models.finance.inv.item import Item
            from app.models.finance.inv.warehouse import Warehouse

            if model_class == Item or str(model_class) == "<class 'app.models.ifrs.inv.item.Item'>":
                return mock_item
            elif model_class == Warehouse or str(model_class) == "<class 'app.models.ifrs.inv.warehouse.Warehouse'>":
                return mock_warehouse
            return None

        mock_db.get.side_effect = mock_get

        input_data = create_transaction_input(
            transaction_type=TransactionType.RECEIPT,
            item_id=item_id,
            warehouse_id=wh_id,
        )

        with pytest.raises(HTTPException) as exc_info:
            InventoryTransactionService.create_receipt(
                db=mock_db,
                organization_id=org_id,
                input=input_data,
                created_by_user_id=user_id,
            )

        assert exc_info.value.status_code == 400
        assert "not configured for receiving" in exc_info.value.detail

    def test_create_receipt_standard_costing_variance(self):
        """Test receipt with standard costing calculates variance."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        item_id = uuid.uuid4()
        wh_id = uuid.uuid4()

        mock_item = MockItem(
            item_id=item_id,
            organization_id=org_id,
            costing_method=CostingMethod.STANDARD_COST,
            standard_cost=Decimal("10.00"),
        )
        mock_warehouse = MockWarehouse(
            warehouse_id=wh_id,
            organization_id=org_id,
            is_receiving=True,
        )

        def mock_get(model_class, id_val):
            from app.models.finance.inv.item import Item
            from app.models.finance.inv.warehouse import Warehouse

            if model_class == Item or str(model_class) == "<class 'app.models.ifrs.inv.item.Item'>":
                return mock_item
            elif model_class == Warehouse or str(model_class) == "<class 'app.models.ifrs.inv.warehouse.Warehouse'>":
                return mock_warehouse
            return None

        mock_db.get.side_effect = mock_get

        input_data = create_transaction_input(
            transaction_type=TransactionType.RECEIPT,
            item_id=item_id,
            warehouse_id=wh_id,
            quantity=Decimal("100"),
            unit_cost=Decimal("12.00"),  # Actual cost > standard cost
        )

        with patch.object(
            InventoryTransactionService,
            'get_current_balance',
            return_value=Decimal("0")
        ):
            result = InventoryTransactionService.create_receipt(
                db=mock_db,
                organization_id=org_id,
                input=input_data,
                created_by_user_id=user_id,
            )

        # Verify transaction was added with variance
        mock_db.add.assert_called_once()
        # Check that the item's last_purchase_cost was updated
        assert mock_item.last_purchase_cost == Decimal("12.00")


class TestCreateIssue:
    """Tests for create_issue method."""

    def test_create_issue_success(self):
        """Test successful issue creation."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        item_id = uuid.uuid4()
        wh_id = uuid.uuid4()

        mock_item = MockItem(
            item_id=item_id,
            organization_id=org_id,
            costing_method=CostingMethod.WEIGHTED_AVERAGE,
            average_cost=Decimal("10.00"),
            track_lots=False,
        )
        mock_warehouse = MockWarehouse(
            warehouse_id=wh_id,
            organization_id=org_id,
        )

        def mock_get(model_class, id_val):
            from app.models.finance.inv.item import Item
            from app.models.finance.inv.warehouse import Warehouse

            if model_class == Item or str(model_class) == "<class 'app.models.ifrs.inv.item.Item'>":
                return mock_item
            elif model_class == Warehouse or str(model_class) == "<class 'app.models.ifrs.inv.warehouse.Warehouse'>":
                return mock_warehouse
            return None

        mock_db.get.side_effect = mock_get

        input_data = create_transaction_input(
            transaction_type=TransactionType.ISSUE,
            item_id=item_id,
            warehouse_id=wh_id,
            quantity=Decimal("50"),
        )

        with patch.object(
            InventoryTransactionService,
            'get_current_balance',
            return_value=Decimal("100")  # Sufficient balance
        ):
            result = InventoryTransactionService.create_issue(
                db=mock_db,
                organization_id=org_id,
                input=input_data,
                created_by_user_id=user_id,
            )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_create_issue_insufficient_inventory(self):
        """Test issue fails when insufficient inventory."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        item_id = uuid.uuid4()
        wh_id = uuid.uuid4()

        mock_item = MockItem(item_id=item_id, organization_id=org_id, track_lots=False)
        mock_warehouse = MockWarehouse(warehouse_id=wh_id, organization_id=org_id)

        def mock_get(model_class, id_val):
            from app.models.finance.inv.item import Item
            from app.models.finance.inv.warehouse import Warehouse

            if model_class == Item or str(model_class) == "<class 'app.models.ifrs.inv.item.Item'>":
                return mock_item
            elif model_class == Warehouse or str(model_class) == "<class 'app.models.ifrs.inv.warehouse.Warehouse'>":
                return mock_warehouse
            return None

        mock_db.get.side_effect = mock_get

        input_data = create_transaction_input(
            transaction_type=TransactionType.ISSUE,
            item_id=item_id,
            warehouse_id=wh_id,
            quantity=Decimal("100"),  # Try to issue 100
        )

        with patch.object(
            InventoryTransactionService,
            'get_current_balance',
            return_value=Decimal("50")  # Only 50 available
        ):
            with pytest.raises(HTTPException) as exc_info:
                InventoryTransactionService.create_issue(
                    db=mock_db,
                    organization_id=org_id,
                    input=input_data,
                    created_by_user_id=user_id,
                )

        assert exc_info.value.status_code == 400
        assert "Insufficient inventory" in exc_info.value.detail

    def test_create_issue_lot_tracked_without_lot_id(self):
        """Test issue fails when lot-tracked item without lot ID."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        item_id = uuid.uuid4()
        wh_id = uuid.uuid4()

        mock_item = MockItem(
            item_id=item_id,
            organization_id=org_id,
            track_lots=True,  # Lot-tracked
        )
        mock_warehouse = MockWarehouse(warehouse_id=wh_id, organization_id=org_id)

        def mock_get(model_class, id_val):
            from app.models.finance.inv.item import Item
            from app.models.finance.inv.warehouse import Warehouse

            if model_class == Item or str(model_class) == "<class 'app.models.ifrs.inv.item.Item'>":
                return mock_item
            elif model_class == Warehouse or str(model_class) == "<class 'app.models.ifrs.inv.warehouse.Warehouse'>":
                return mock_warehouse
            return None

        mock_db.get.side_effect = mock_get

        input_data = create_transaction_input(
            transaction_type=TransactionType.ISSUE,
            item_id=item_id,
            warehouse_id=wh_id,
            quantity=Decimal("10"),
            lot_id=None,  # No lot ID provided
        )

        with patch.object(
            InventoryTransactionService,
            'get_current_balance',
            return_value=Decimal("100")
        ):
            with pytest.raises(HTTPException) as exc_info:
                InventoryTransactionService.create_issue(
                    db=mock_db,
                    organization_id=org_id,
                    input=input_data,
                    created_by_user_id=user_id,
                )

        assert exc_info.value.status_code == 400
        assert "Lot ID is required" in exc_info.value.detail

    def test_create_issue_lot_quarantined(self):
        """Test issue fails when lot is quarantined."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        item_id = uuid.uuid4()
        wh_id = uuid.uuid4()
        lot_id = uuid.uuid4()

        mock_item = MockItem(
            item_id=item_id,
            organization_id=org_id,
            track_lots=True,
        )
        mock_warehouse = MockWarehouse(warehouse_id=wh_id, organization_id=org_id)
        mock_lot = MockInventoryLot(
            lot_id=lot_id,
            item_id=item_id,
            is_quarantined=True,  # Quarantined
        )

        def mock_get(model_class, id_val):
            from app.models.finance.inv.item import Item
            from app.models.finance.inv.warehouse import Warehouse
            from app.models.finance.inv.inventory_lot import InventoryLot

            if model_class == Item or str(model_class) == "<class 'app.models.ifrs.inv.item.Item'>":
                return mock_item
            elif model_class == Warehouse or str(model_class) == "<class 'app.models.ifrs.inv.warehouse.Warehouse'>":
                return mock_warehouse
            elif model_class == InventoryLot or str(model_class) == "<class 'app.models.ifrs.inv.inventory_lot.InventoryLot'>":
                return mock_lot
            return None

        mock_db.get.side_effect = mock_get

        input_data = create_transaction_input(
            transaction_type=TransactionType.ISSUE,
            item_id=item_id,
            warehouse_id=wh_id,
            quantity=Decimal("10"),
            lot_id=lot_id,
        )

        with patch.object(
            InventoryTransactionService,
            'get_current_balance',
            return_value=Decimal("100")
        ):
            with pytest.raises(HTTPException) as exc_info:
                InventoryTransactionService.create_issue(
                    db=mock_db,
                    organization_id=org_id,
                    input=input_data,
                    created_by_user_id=user_id,
                )

        assert exc_info.value.status_code == 400
        assert "quarantined" in exc_info.value.detail

    def test_create_issue_lot_insufficient_quantity(self):
        """Test issue fails when lot has insufficient quantity."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        item_id = uuid.uuid4()
        wh_id = uuid.uuid4()
        lot_id = uuid.uuid4()

        mock_item = MockItem(
            item_id=item_id,
            organization_id=org_id,
            track_lots=True,
        )
        mock_warehouse = MockWarehouse(warehouse_id=wh_id, organization_id=org_id)
        mock_lot = MockInventoryLot(
            lot_id=lot_id,
            item_id=item_id,
            quantity_available=Decimal("10"),  # Only 10 available in lot
        )

        def mock_get(model_class, id_val):
            from app.models.finance.inv.item import Item
            from app.models.finance.inv.warehouse import Warehouse
            from app.models.finance.inv.inventory_lot import InventoryLot

            if model_class == Item or str(model_class) == "<class 'app.models.ifrs.inv.item.Item'>":
                return mock_item
            elif model_class == Warehouse or str(model_class) == "<class 'app.models.ifrs.inv.warehouse.Warehouse'>":
                return mock_warehouse
            elif model_class == InventoryLot or str(model_class) == "<class 'app.models.ifrs.inv.inventory_lot.InventoryLot'>":
                return mock_lot
            return None

        mock_db.get.side_effect = mock_get

        input_data = create_transaction_input(
            transaction_type=TransactionType.ISSUE,
            item_id=item_id,
            warehouse_id=wh_id,
            quantity=Decimal("50"),  # Request 50
            lot_id=lot_id,
        )

        with patch.object(
            InventoryTransactionService,
            'get_current_balance',
            return_value=Decimal("100")
        ):
            with pytest.raises(HTTPException) as exc_info:
                InventoryTransactionService.create_issue(
                    db=mock_db,
                    organization_id=org_id,
                    input=input_data,
                    created_by_user_id=user_id,
                )

        assert exc_info.value.status_code == 400
        assert "Insufficient quantity in lot" in exc_info.value.detail

    def test_create_issue_specific_identification_requires_lot(self):
        """Test issue with specific identification costing requires lot ID."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        item_id = uuid.uuid4()
        wh_id = uuid.uuid4()

        mock_item = MockItem(
            item_id=item_id,
            organization_id=org_id,
            costing_method=CostingMethod.SPECIFIC_IDENTIFICATION,
            track_lots=False,
        )
        mock_warehouse = MockWarehouse(warehouse_id=wh_id, organization_id=org_id)

        def mock_get(model_class, id_val):
            from app.models.finance.inv.item import Item
            from app.models.finance.inv.warehouse import Warehouse

            if model_class == Item or str(model_class) == "<class 'app.models.ifrs.inv.item.Item'>":
                return mock_item
            elif model_class == Warehouse or str(model_class) == "<class 'app.models.ifrs.inv.warehouse.Warehouse'>":
                return mock_warehouse
            return None

        mock_db.get.side_effect = mock_get

        input_data = create_transaction_input(
            transaction_type=TransactionType.ISSUE,
            item_id=item_id,
            warehouse_id=wh_id,
            quantity=Decimal("10"),
            lot_id=None,
        )

        with patch.object(
            InventoryTransactionService,
            'get_current_balance',
            return_value=Decimal("100")
        ):
            with pytest.raises(HTTPException) as exc_info:
                InventoryTransactionService.create_issue(
                    db=mock_db,
                    organization_id=org_id,
                    input=input_data,
                    created_by_user_id=user_id,
                )

        assert exc_info.value.status_code == 400
        assert "Lot ID required for specific identification" in exc_info.value.detail


class TestConsumeFifo:
    """Tests for _consume_fifo method."""

    def test_consume_fifo_single_lot(self):
        """Test FIFO consumption from single lot."""
        mock_db = MagicMock()
        item_id = uuid.uuid4()

        mock_lot = MockInventoryLot(
            lot_id=uuid.uuid4(),
            item_id=item_id,
            quantity_on_hand=Decimal("100"),
            quantity_allocated=Decimal("0"),
            unit_cost=Decimal("10.00"),
        )

        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [mock_lot]

        result = InventoryTransactionService._consume_fifo(
            db=mock_db,
            item_id=item_id,
            quantity=Decimal("50"),
        )

        assert result["unit_cost"] == Decimal("10.000000")
        assert result["total_cost"] == Decimal("500.00")
        assert len(result["layers_used"]) == 1
        assert mock_lot.quantity_on_hand == Decimal("50")

    def test_consume_fifo_multiple_lots(self):
        """Test FIFO consumption from multiple lots."""
        mock_db = MagicMock()
        item_id = uuid.uuid4()

        lot1 = MockInventoryLot(
            lot_id=uuid.uuid4(),
            item_id=item_id,
            quantity_on_hand=Decimal("30"),
            quantity_allocated=Decimal("0"),
            unit_cost=Decimal("10.00"),
        )
        lot2 = MockInventoryLot(
            lot_id=uuid.uuid4(),
            item_id=item_id,
            quantity_on_hand=Decimal("50"),
            quantity_allocated=Decimal("0"),
            unit_cost=Decimal("12.00"),
        )

        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [lot1, lot2]

        result = InventoryTransactionService._consume_fifo(
            db=mock_db,
            item_id=item_id,
            quantity=Decimal("50"),  # 30 from lot1 + 20 from lot2
        )

        # (30 * 10 + 20 * 12) / 50 = 540 / 50 = 10.8
        assert result["unit_cost"] == Decimal("10.800000")
        assert result["total_cost"] == Decimal("540.00")
        assert len(result["layers_used"]) == 2
        assert lot1.quantity_on_hand == Decimal("0")
        assert lot2.quantity_on_hand == Decimal("30")

    def test_consume_fifo_insufficient_inventory(self):
        """Test FIFO consumption fails with insufficient inventory."""
        mock_db = MagicMock()
        item_id = uuid.uuid4()

        mock_lot = MockInventoryLot(
            lot_id=uuid.uuid4(),
            item_id=item_id,
            quantity_on_hand=Decimal("30"),
            quantity_allocated=Decimal("0"),
            unit_cost=Decimal("10.00"),
        )

        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [mock_lot]

        with pytest.raises(HTTPException) as exc_info:
            InventoryTransactionService._consume_fifo(
                db=mock_db,
                item_id=item_id,
                quantity=Decimal("50"),  # More than available
            )

        assert exc_info.value.status_code == 400
        assert "Insufficient FIFO inventory" in exc_info.value.detail


class TestConsumeFromLot:
    """Tests for _consume_from_lot method."""

    def test_consume_from_lot_success(self):
        """Test consuming from specific lot."""
        mock_db = MagicMock()
        lot_id = uuid.uuid4()

        mock_lot = MockInventoryLot(
            lot_id=lot_id,
            quantity_on_hand=Decimal("100"),
            quantity_allocated=Decimal("10"),
        )

        mock_db.get.return_value = mock_lot

        InventoryTransactionService._consume_from_lot(
            db=mock_db,
            lot_id=lot_id,
            quantity=Decimal("30"),
        )

        assert mock_lot.quantity_on_hand == Decimal("70")
        assert mock_lot.quantity_available == Decimal("60")  # 70 - 10


class TestCreateAdjustment:
    """Tests for create_adjustment method."""

    def test_create_positive_adjustment(self):
        """Test creating positive inventory adjustment."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        item_id = uuid.uuid4()
        wh_id = uuid.uuid4()

        mock_item = MockItem(
            item_id=item_id,
            organization_id=org_id,
            average_cost=Decimal("10.00"),
        )
        mock_warehouse = MockWarehouse(warehouse_id=wh_id, organization_id=org_id)

        def mock_get(model_class, id_val):
            from app.models.finance.inv.item import Item
            from app.models.finance.inv.warehouse import Warehouse

            if model_class == Item or str(model_class) == "<class 'app.models.ifrs.inv.item.Item'>":
                return mock_item
            elif model_class == Warehouse or str(model_class) == "<class 'app.models.ifrs.inv.warehouse.Warehouse'>":
                return mock_warehouse
            return None

        mock_db.get.side_effect = mock_get

        input_data = create_transaction_input(
            transaction_type=TransactionType.ADJUSTMENT,
            item_id=item_id,
            warehouse_id=wh_id,
            quantity=Decimal("50"),  # Positive
        )

        with patch.object(
            InventoryTransactionService,
            'get_current_balance',
            return_value=Decimal("100")
        ):
            result = InventoryTransactionService.create_adjustment(
                db=mock_db,
                organization_id=org_id,
                input=input_data,
                created_by_user_id=user_id,
            )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_create_negative_adjustment(self):
        """Test creating negative inventory adjustment."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        item_id = uuid.uuid4()
        wh_id = uuid.uuid4()

        mock_item = MockItem(
            item_id=item_id,
            organization_id=org_id,
            average_cost=Decimal("10.00"),
        )
        mock_warehouse = MockWarehouse(warehouse_id=wh_id, organization_id=org_id)

        def mock_get(model_class, id_val):
            from app.models.finance.inv.item import Item
            from app.models.finance.inv.warehouse import Warehouse

            if model_class == Item or str(model_class) == "<class 'app.models.ifrs.inv.item.Item'>":
                return mock_item
            elif model_class == Warehouse or str(model_class) == "<class 'app.models.ifrs.inv.warehouse.Warehouse'>":
                return mock_warehouse
            return None

        mock_db.get.side_effect = mock_get

        input_data = create_transaction_input(
            transaction_type=TransactionType.ADJUSTMENT,
            item_id=item_id,
            warehouse_id=wh_id,
            quantity=Decimal("-30"),  # Negative
        )

        with patch.object(
            InventoryTransactionService,
            'get_current_balance',
            return_value=Decimal("100")
        ):
            result = InventoryTransactionService.create_adjustment(
                db=mock_db,
                organization_id=org_id,
                input=input_data,
                created_by_user_id=user_id,
            )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_create_adjustment_would_go_negative(self):
        """Test adjustment fails when result would be negative inventory."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        item_id = uuid.uuid4()
        wh_id = uuid.uuid4()

        mock_item = MockItem(item_id=item_id, organization_id=org_id)
        mock_warehouse = MockWarehouse(warehouse_id=wh_id, organization_id=org_id)

        def mock_get(model_class, id_val):
            from app.models.finance.inv.item import Item
            from app.models.finance.inv.warehouse import Warehouse

            if model_class == Item or str(model_class) == "<class 'app.models.ifrs.inv.item.Item'>":
                return mock_item
            elif model_class == Warehouse or str(model_class) == "<class 'app.models.ifrs.inv.warehouse.Warehouse'>":
                return mock_warehouse
            return None

        mock_db.get.side_effect = mock_get

        input_data = create_transaction_input(
            transaction_type=TransactionType.ADJUSTMENT,
            item_id=item_id,
            warehouse_id=wh_id,
            quantity=Decimal("-150"),  # More than available
        )

        with patch.object(
            InventoryTransactionService,
            'get_current_balance',
            return_value=Decimal("100")
        ):
            with pytest.raises(HTTPException) as exc_info:
                InventoryTransactionService.create_adjustment(
                    db=mock_db,
                    organization_id=org_id,
                    input=input_data,
                    created_by_user_id=user_id,
                )

        assert exc_info.value.status_code == 400
        assert "negative inventory" in exc_info.value.detail


class TestCreateTransfer:
    """Tests for create_transfer method."""

    def test_create_transfer_success(self):
        """Test successful inventory transfer."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        item_id = uuid.uuid4()
        from_wh_id = uuid.uuid4()
        to_wh_id = uuid.uuid4()

        mock_item = MockItem(
            item_id=item_id,
            organization_id=org_id,
            average_cost=Decimal("10.00"),
        )
        mock_from_warehouse = MockWarehouse(warehouse_id=from_wh_id, organization_id=org_id)
        mock_to_warehouse = MockWarehouse(warehouse_id=to_wh_id, organization_id=org_id)

        call_count = [0]

        def mock_get(model_class, id_val):
            from app.models.finance.inv.item import Item
            from app.models.finance.inv.warehouse import Warehouse

            if model_class == Item or str(model_class) == "<class 'app.models.ifrs.inv.item.Item'>":
                return mock_item
            elif model_class == Warehouse or str(model_class) == "<class 'app.models.ifrs.inv.warehouse.Warehouse'>":
                call_count[0] += 1
                if call_count[0] == 1:
                    return mock_from_warehouse
                else:
                    return mock_to_warehouse
            return None

        mock_db.get.side_effect = mock_get

        input_data = create_transaction_input(
            transaction_type=TransactionType.TRANSFER,
            item_id=item_id,
            warehouse_id=from_wh_id,
            to_warehouse_id=to_wh_id,
            quantity=Decimal("50"),
        )

        # Mock balances for both warehouses
        with patch.object(
            InventoryTransactionService,
            'get_current_balance'
        ) as mock_balance:
            mock_balance.side_effect = [Decimal("100"), Decimal("0")]  # from, to

            issue_txn, receipt_txn = InventoryTransactionService.create_transfer(
                db=mock_db,
                organization_id=org_id,
                input=input_data,
                created_by_user_id=user_id,
            )

        assert mock_db.add.call_count == 2  # Issue and receipt transactions
        mock_db.commit.assert_called_once()

    def test_create_transfer_missing_to_warehouse(self):
        """Test transfer fails when to_warehouse_id missing."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()

        input_data = create_transaction_input(
            transaction_type=TransactionType.TRANSFER,
            to_warehouse_id=None,  # Missing
        )

        with pytest.raises(HTTPException) as exc_info:
            InventoryTransactionService.create_transfer(
                db=mock_db,
                organization_id=org_id,
                input=input_data,
                created_by_user_id=user_id,
            )

        assert exc_info.value.status_code == 400
        assert "to_warehouse_id is required" in exc_info.value.detail

    def test_create_transfer_insufficient_source_inventory(self):
        """Test transfer fails when insufficient inventory at source."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        item_id = uuid.uuid4()
        from_wh_id = uuid.uuid4()
        to_wh_id = uuid.uuid4()

        mock_item = MockItem(item_id=item_id, organization_id=org_id)
        mock_from_warehouse = MockWarehouse(warehouse_id=from_wh_id, organization_id=org_id)
        mock_to_warehouse = MockWarehouse(warehouse_id=to_wh_id, organization_id=org_id)

        call_count = [0]

        def mock_get(model_class, id_val):
            from app.models.finance.inv.item import Item
            from app.models.finance.inv.warehouse import Warehouse

            if model_class == Item or str(model_class) == "<class 'app.models.ifrs.inv.item.Item'>":
                return mock_item
            elif model_class == Warehouse or str(model_class) == "<class 'app.models.ifrs.inv.warehouse.Warehouse'>":
                call_count[0] += 1
                if call_count[0] == 1:
                    return mock_from_warehouse
                else:
                    return mock_to_warehouse
            return None

        mock_db.get.side_effect = mock_get

        input_data = create_transaction_input(
            transaction_type=TransactionType.TRANSFER,
            item_id=item_id,
            warehouse_id=from_wh_id,
            to_warehouse_id=to_wh_id,
            quantity=Decimal("100"),  # Request 100
        )

        with patch.object(
            InventoryTransactionService,
            'get_current_balance',
            return_value=Decimal("50")  # Only 50 at source
        ):
            with pytest.raises(HTTPException) as exc_info:
                InventoryTransactionService.create_transfer(
                    db=mock_db,
                    organization_id=org_id,
                    input=input_data,
                    created_by_user_id=user_id,
                )

        assert exc_info.value.status_code == 400
        assert "Insufficient inventory at source" in exc_info.value.detail


class TestGet:
    """Tests for get method."""

    def test_get_transaction_success(self):
        """Test getting a transaction by ID."""
        mock_db = MagicMock()
        txn_id = uuid.uuid4()

        mock_transaction = MockInventoryTransaction(transaction_id=txn_id)
        mock_db.get.return_value = mock_transaction

        result = InventoryTransactionService.get(db=mock_db, transaction_id=str(txn_id))

        assert result == mock_transaction

    def test_get_transaction_not_found(self):
        """Test getting non-existent transaction raises error."""
        mock_db = MagicMock()
        txn_id = uuid.uuid4()

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            InventoryTransactionService.get(db=mock_db, transaction_id=str(txn_id))

        assert exc_info.value.status_code == 404
        assert "Transaction not found" in exc_info.value.detail


class TestCreateTransaction:
    """Tests for create_transaction router method."""

    @patch.object(InventoryTransactionService, 'create_receipt')
    def test_create_transaction_routes_receipt(self, mock_create_receipt):
        """Test create_transaction routes RECEIPT to create_receipt."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()

        input_data = create_transaction_input(transaction_type=TransactionType.RECEIPT)

        InventoryTransactionService.create_transaction(
            db=mock_db,
            organization_id=org_id,
            input=input_data,
            created_by_user_id=user_id,
        )

        mock_create_receipt.assert_called_once()

    @patch.object(InventoryTransactionService, 'create_receipt')
    def test_create_transaction_routes_return(self, mock_create_receipt):
        """Test create_transaction routes RETURN to create_receipt."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()

        input_data = create_transaction_input(transaction_type=TransactionType.RETURN)

        InventoryTransactionService.create_transaction(
            db=mock_db,
            organization_id=org_id,
            input=input_data,
            created_by_user_id=user_id,
        )

        mock_create_receipt.assert_called_once()

    @patch.object(InventoryTransactionService, 'create_issue')
    def test_create_transaction_routes_issue(self, mock_create_issue):
        """Test create_transaction routes ISSUE to create_issue."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()

        input_data = create_transaction_input(transaction_type=TransactionType.ISSUE)

        InventoryTransactionService.create_transaction(
            db=mock_db,
            organization_id=org_id,
            input=input_data,
            created_by_user_id=user_id,
        )

        mock_create_issue.assert_called_once()

    @patch.object(InventoryTransactionService, 'create_issue')
    def test_create_transaction_routes_sale(self, mock_create_issue):
        """Test create_transaction routes SALE to create_issue."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()

        input_data = create_transaction_input(transaction_type=TransactionType.SALE)

        InventoryTransactionService.create_transaction(
            db=mock_db,
            organization_id=org_id,
            input=input_data,
            created_by_user_id=user_id,
        )

        mock_create_issue.assert_called_once()

    @patch.object(InventoryTransactionService, 'create_transfer')
    def test_create_transaction_routes_transfer(self, mock_create_transfer):
        """Test create_transaction routes TRANSFER to create_transfer."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()

        mock_create_transfer.return_value = (MagicMock(), MagicMock())

        input_data = create_transaction_input(
            transaction_type=TransactionType.TRANSFER,
            to_warehouse_id=uuid.uuid4(),
        )

        InventoryTransactionService.create_transaction(
            db=mock_db,
            organization_id=org_id,
            input=input_data,
            created_by_user_id=user_id,
        )

        mock_create_transfer.assert_called_once()

    @patch.object(InventoryTransactionService, 'create_adjustment')
    def test_create_transaction_routes_adjustment(self, mock_create_adjustment):
        """Test create_transaction routes ADJUSTMENT to create_adjustment."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()

        input_data = create_transaction_input(transaction_type=TransactionType.ADJUSTMENT)

        InventoryTransactionService.create_transaction(
            db=mock_db,
            organization_id=org_id,
            input=input_data,
            created_by_user_id=user_id,
        )

        mock_create_adjustment.assert_called_once()

    @patch.object(InventoryTransactionService, 'create_adjustment')
    def test_create_transaction_routes_scrap(self, mock_create_adjustment):
        """Test create_transaction routes SCRAP to create_adjustment."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()

        input_data = create_transaction_input(transaction_type=TransactionType.SCRAP)

        InventoryTransactionService.create_transaction(
            db=mock_db,
            organization_id=org_id,
            input=input_data,
            created_by_user_id=user_id,
        )

        mock_create_adjustment.assert_called_once()


class TestList:
    """Tests for list method."""

    def test_list_with_no_filters(self):
        """Test listing transactions without filters."""
        mock_db = MagicMock()
        mock_transactions = [MockInventoryTransaction(), MockInventoryTransaction()]

        mock_db.query.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_transactions

        result = InventoryTransactionService.list(db=mock_db)

        assert len(result) == 2

    def test_list_with_organization_filter(self):
        """Test listing transactions with organization filter."""
        mock_db = MagicMock()
        org_id = uuid.uuid4()
        mock_transactions = [MockInventoryTransaction()]

        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_transactions

        result = InventoryTransactionService.list(
            db=mock_db,
            organization_id=str(org_id),
        )

        assert len(result) == 1

    def test_list_with_item_filter(self):
        """Test listing transactions with item filter."""
        mock_db = MagicMock()
        item_id = uuid.uuid4()
        mock_transactions = [MockInventoryTransaction()]

        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_transactions

        result = InventoryTransactionService.list(
            db=mock_db,
            item_id=str(item_id),
        )

        assert len(result) == 1

    def test_list_with_transaction_type_filter(self):
        """Test listing transactions with transaction type filter."""
        mock_db = MagicMock()
        mock_transactions = [MockInventoryTransaction()]

        # Build the chain
        mock_query = mock_db.query.return_value
        mock_filter1 = mock_query.filter.return_value
        mock_order = mock_filter1.order_by.return_value
        mock_limit = mock_order.limit.return_value
        mock_offset = mock_limit.offset.return_value
        mock_offset.all.return_value = mock_transactions

        result = InventoryTransactionService.list(
            db=mock_db,
            transaction_type=TransactionType.RECEIPT,
        )

        assert len(result) == 1

    def test_list_with_date_range_filter(self):
        """Test listing transactions with date range filter."""
        mock_db = MagicMock()
        mock_transactions = [MockInventoryTransaction()]

        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_transactions

        result = InventoryTransactionService.list(
            db=mock_db,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )

        assert len(result) == 1

    def test_list_with_pagination(self):
        """Test listing transactions with pagination."""
        mock_db = MagicMock()
        mock_transactions = [MockInventoryTransaction()]

        mock_db.query.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_transactions

        result = InventoryTransactionService.list(
            db=mock_db,
            limit=10,
            offset=5,
        )

        assert len(result) == 1
        mock_db.query.return_value.order_by.return_value.limit.assert_called_with(10)
        mock_db.query.return_value.order_by.return_value.limit.return_value.offset.assert_called_with(5)
