"""
Tests for InventoryTransactionService.
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.services.inventory.transaction import (
    InventoryTransactionService,
    TransactionInput,
)
from app.models.inventory.inventory_transaction import TransactionType
from tests.ifrs.inv.conftest import (
    MockItem,
    MockWarehouse,
    MockInventoryTransaction,
    MockCostingMethod,
)


@pytest.fixture
def service():
    """Create InventoryTransactionService instance."""
    return InventoryTransactionService()


@pytest.fixture
def org_id():
    """Create test organization ID."""
    return uuid4()


@pytest.fixture
def user_id():
    """Create test user ID."""
    return uuid4()


@pytest.fixture
def sample_receipt_input():
    """Create sample receipt transaction input."""
    return TransactionInput(
        transaction_type=TransactionType.RECEIPT,
        transaction_date=datetime.now(timezone.utc),
        fiscal_period_id=uuid4(),
        item_id=uuid4(),
        warehouse_id=uuid4(),
        quantity=Decimal("100"),
        unit_cost=Decimal("10.00"),
        uom="EACH",
        currency_code="USD",
    )


@pytest.fixture
def sample_issue_input():
    """Create sample issue transaction input."""
    return TransactionInput(
        transaction_type=TransactionType.ISSUE,
        transaction_date=datetime.now(timezone.utc),
        fiscal_period_id=uuid4(),
        item_id=uuid4(),
        warehouse_id=uuid4(),
        quantity=Decimal("50"),
        unit_cost=Decimal("10.00"),
        uom="EACH",
        currency_code="USD",
    )


# Note: get_current_balance and calculate_weighted_average_cost use SQLAlchemy
# func.case() which requires actual SQLAlchemy models. These methods are tested
# indirectly via integration tests. Unit tests patch these methods instead.


# calculate_weighted_average_cost also uses func.case() and is tested indirectly


class TestCreateReceipt:
    """Tests for create_receipt method."""

    def test_create_receipt_success(
        self, service, mock_db, org_id, user_id, sample_receipt_input
    ):
        """Test successful receipt creation."""
        from app.services.inventory.transaction import InventoryTransactionService

        item = MockItem(
            item_id=sample_receipt_input.item_id,
            organization_id=org_id,
            costing_method=MockCostingMethod.WEIGHTED_AVERAGE,
        )
        warehouse = MockWarehouse(
            warehouse_id=sample_receipt_input.warehouse_id,
            organization_id=org_id,
            is_receiving=True,
        )

        mock_db.get.side_effect = [item, warehouse]
        mock_db.query.return_value.filter.return_value.scalar.return_value = Decimal(
            "0"
        )

        with patch.object(
            InventoryTransactionService,
            "get_current_balance",
            return_value=Decimal("0"),
        ):
            with patch.object(
                InventoryTransactionService,
                "calculate_weighted_average_cost",
                return_value=Decimal("10.00"),
            ):
                result = service.create_receipt(
                    mock_db, org_id, sample_receipt_input, user_id
                )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once()

    def test_create_receipt_item_not_found(
        self, service, mock_db, org_id, user_id, sample_receipt_input
    ):
        """Test receipt creation with invalid item."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.create_receipt(mock_db, org_id, sample_receipt_input, user_id)

        assert exc.value.status_code == 404
        assert "Item not found" in exc.value.detail

    def test_create_receipt_warehouse_not_found(
        self, service, mock_db, org_id, user_id, sample_receipt_input
    ):
        """Test receipt creation with invalid warehouse."""
        from fastapi import HTTPException

        item = MockItem(
            item_id=sample_receipt_input.item_id,
            organization_id=org_id,
        )
        mock_db.get.side_effect = [item, None]

        with pytest.raises(HTTPException) as exc:
            service.create_receipt(mock_db, org_id, sample_receipt_input, user_id)

        assert exc.value.status_code == 404
        assert "Warehouse not found" in exc.value.detail

    def test_create_receipt_warehouse_not_receiving(
        self, service, mock_db, org_id, user_id, sample_receipt_input
    ):
        """Test receipt creation with non-receiving warehouse."""
        from fastapi import HTTPException

        item = MockItem(
            item_id=sample_receipt_input.item_id,
            organization_id=org_id,
        )
        warehouse = MockWarehouse(
            warehouse_id=sample_receipt_input.warehouse_id,
            organization_id=org_id,
            is_receiving=False,
        )
        mock_db.get.side_effect = [item, warehouse]

        with pytest.raises(HTTPException) as exc:
            service.create_receipt(mock_db, org_id, sample_receipt_input, user_id)

        assert exc.value.status_code == 400
        assert "not configured for receiving" in exc.value.detail


class TestCreateIssue:
    """Tests for create_issue method."""

    def test_create_issue_success(
        self, service, mock_db, org_id, user_id, sample_issue_input
    ):
        """Test successful issue creation."""
        from app.services.inventory.transaction import InventoryTransactionService

        item = MockItem(
            item_id=sample_issue_input.item_id,
            organization_id=org_id,
            costing_method=MockCostingMethod.WEIGHTED_AVERAGE,
            average_cost=Decimal("10.00"),
        )
        warehouse = MockWarehouse(
            warehouse_id=sample_issue_input.warehouse_id,
            organization_id=org_id,
        )

        mock_db.get.side_effect = [item, warehouse]

        with patch.object(
            InventoryTransactionService,
            "get_current_balance",
            return_value=Decimal("100"),
        ):
            result = service.create_issue(mock_db, org_id, sample_issue_input, user_id)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_create_issue_insufficient_inventory(
        self, service, mock_db, org_id, user_id, sample_issue_input
    ):
        """Test issue creation with insufficient inventory."""
        from fastapi import HTTPException
        from app.services.inventory.transaction import InventoryTransactionService

        item = MockItem(
            item_id=sample_issue_input.item_id,
            organization_id=org_id,
        )
        warehouse = MockWarehouse(
            warehouse_id=sample_issue_input.warehouse_id,
            organization_id=org_id,
        )

        mock_db.get.side_effect = [item, warehouse]

        with patch.object(
            InventoryTransactionService,
            "get_current_balance",
            return_value=Decimal("10"),
        ):
            with pytest.raises(HTTPException) as exc:
                service.create_issue(mock_db, org_id, sample_issue_input, user_id)

        assert exc.value.status_code == 400
        assert "Insufficient inventory" in exc.value.detail

    def test_create_issue_item_not_found(
        self, service, mock_db, org_id, user_id, sample_issue_input
    ):
        """Test issue creation with invalid item."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.create_issue(mock_db, org_id, sample_issue_input, user_id)

        assert exc.value.status_code == 404


class TestCreateAdjustment:
    """Tests for create_adjustment method."""

    def test_create_positive_adjustment(self, service, mock_db, org_id, user_id):
        """Test successful positive adjustment."""
        from app.services.inventory.transaction import InventoryTransactionService

        input_data = TransactionInput(
            transaction_type=TransactionType.ADJUSTMENT,
            transaction_date=datetime.now(timezone.utc),
            fiscal_period_id=uuid4(),
            item_id=uuid4(),
            warehouse_id=uuid4(),
            quantity=Decimal("50"),  # Positive adjustment
            unit_cost=Decimal("10.00"),
            uom="EACH",
            currency_code="USD",
            reason_code="CYCLE_COUNT",
        )

        item = MockItem(
            item_id=input_data.item_id,
            organization_id=org_id,
            average_cost=Decimal("10.00"),
        )
        warehouse = MockWarehouse(
            warehouse_id=input_data.warehouse_id,
            organization_id=org_id,
        )

        mock_db.get.side_effect = [item, warehouse]

        with patch.object(
            InventoryTransactionService,
            "get_current_balance",
            return_value=Decimal("100"),
        ):
            result = service.create_adjustment(mock_db, org_id, input_data, user_id)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_create_negative_adjustment(self, service, mock_db, org_id, user_id):
        """Test successful negative adjustment."""
        from app.services.inventory.transaction import InventoryTransactionService

        input_data = TransactionInput(
            transaction_type=TransactionType.ADJUSTMENT,
            transaction_date=datetime.now(timezone.utc),
            fiscal_period_id=uuid4(),
            item_id=uuid4(),
            warehouse_id=uuid4(),
            quantity=Decimal("-30"),  # Negative adjustment
            unit_cost=Decimal("10.00"),
            uom="EACH",
            currency_code="USD",
        )

        item = MockItem(
            item_id=input_data.item_id,
            organization_id=org_id,
            average_cost=Decimal("10.00"),
        )
        warehouse = MockWarehouse(
            warehouse_id=input_data.warehouse_id,
            organization_id=org_id,
        )

        mock_db.get.side_effect = [item, warehouse]

        with patch.object(
            InventoryTransactionService,
            "get_current_balance",
            return_value=Decimal("100"),
        ):
            result = service.create_adjustment(mock_db, org_id, input_data, user_id)

        mock_db.add.assert_called_once()

    def test_create_adjustment_negative_inventory_fails(
        self, service, mock_db, org_id, user_id
    ):
        """Test that adjustment resulting in negative inventory fails."""
        from fastapi import HTTPException
        from app.services.inventory.transaction import InventoryTransactionService

        input_data = TransactionInput(
            transaction_type=TransactionType.ADJUSTMENT,
            transaction_date=datetime.now(timezone.utc),
            fiscal_period_id=uuid4(),
            item_id=uuid4(),
            warehouse_id=uuid4(),
            quantity=Decimal("-200"),  # More than available
            unit_cost=Decimal("10.00"),
            uom="EACH",
            currency_code="USD",
        )

        item = MockItem(
            item_id=input_data.item_id,
            organization_id=org_id,
        )
        warehouse = MockWarehouse(
            warehouse_id=input_data.warehouse_id,
            organization_id=org_id,
        )

        mock_db.get.side_effect = [item, warehouse]

        with patch.object(
            InventoryTransactionService,
            "get_current_balance",
            return_value=Decimal("100"),
        ):
            with pytest.raises(HTTPException) as exc:
                service.create_adjustment(mock_db, org_id, input_data, user_id)

        assert exc.value.status_code == 400
        assert "negative inventory" in exc.value.detail


class TestCreateTransfer:
    """Tests for create_transfer method."""

    def test_create_transfer_success(self, service, mock_db, org_id, user_id):
        """Test successful transfer between warehouses."""
        from app.services.inventory.transaction import InventoryTransactionService

        input_data = TransactionInput(
            transaction_type=TransactionType.TRANSFER,
            transaction_date=datetime.now(timezone.utc),
            fiscal_period_id=uuid4(),
            item_id=uuid4(),
            warehouse_id=uuid4(),
            to_warehouse_id=uuid4(),
            quantity=Decimal("50"),
            unit_cost=Decimal("10.00"),
            uom="EACH",
            currency_code="USD",
        )

        item = MockItem(
            item_id=input_data.item_id,
            organization_id=org_id,
            average_cost=Decimal("10.00"),
        )
        from_warehouse = MockWarehouse(
            warehouse_id=input_data.warehouse_id,
            organization_id=org_id,
        )
        to_warehouse = MockWarehouse(
            warehouse_id=input_data.to_warehouse_id,
            organization_id=org_id,
        )

        mock_db.get.side_effect = [item, from_warehouse, to_warehouse]

        with patch.object(
            InventoryTransactionService,
            "get_current_balance",
            side_effect=[
                Decimal("100"),  # Source warehouse
                Decimal("50"),  # Destination warehouse
            ],
        ):
            result = service.create_transfer(mock_db, org_id, input_data, user_id)

        # Should create 2 transactions (issue and receipt)
        assert mock_db.add.call_count == 2
        mock_db.commit.assert_called_once()

    def test_create_transfer_no_destination_fails(
        self, service, mock_db, org_id, user_id
    ):
        """Test transfer without destination warehouse fails."""
        from fastapi import HTTPException

        input_data = TransactionInput(
            transaction_type=TransactionType.TRANSFER,
            transaction_date=datetime.now(timezone.utc),
            fiscal_period_id=uuid4(),
            item_id=uuid4(),
            warehouse_id=uuid4(),
            to_warehouse_id=None,  # Missing destination
            quantity=Decimal("50"),
            unit_cost=Decimal("10.00"),
            uom="EACH",
            currency_code="USD",
        )

        with pytest.raises(HTTPException) as exc:
            service.create_transfer(mock_db, org_id, input_data, user_id)

        assert exc.value.status_code == 400
        assert "to_warehouse_id is required" in exc.value.detail

    def test_create_transfer_insufficient_inventory(
        self, service, mock_db, org_id, user_id
    ):
        """Test transfer with insufficient inventory fails."""
        from fastapi import HTTPException
        from app.services.inventory.transaction import InventoryTransactionService

        input_data = TransactionInput(
            transaction_type=TransactionType.TRANSFER,
            transaction_date=datetime.now(timezone.utc),
            fiscal_period_id=uuid4(),
            item_id=uuid4(),
            warehouse_id=uuid4(),
            to_warehouse_id=uuid4(),
            quantity=Decimal("150"),  # More than available
            unit_cost=Decimal("10.00"),
            uom="EACH",
            currency_code="USD",
        )

        item = MockItem(
            item_id=input_data.item_id,
            organization_id=org_id,
        )
        from_warehouse = MockWarehouse(
            warehouse_id=input_data.warehouse_id,
            organization_id=org_id,
        )
        to_warehouse = MockWarehouse(
            warehouse_id=input_data.to_warehouse_id,
            organization_id=org_id,
        )

        mock_db.get.side_effect = [item, from_warehouse, to_warehouse]

        with patch.object(
            InventoryTransactionService,
            "get_current_balance",
            return_value=Decimal("100"),
        ):
            with pytest.raises(HTTPException) as exc:
                service.create_transfer(mock_db, org_id, input_data, user_id)

        assert exc.value.status_code == 400
        assert "Insufficient inventory" in exc.value.detail


class TestListTransactions:
    """Tests for list method."""

    def test_list_all_transactions(self, service, mock_db, org_id):
        """Test listing all transactions."""
        transactions = [
            MockInventoryTransaction(organization_id=org_id) for _ in range(5)
        ]
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = transactions

        result = service.list(mock_db, str(org_id))

        assert len(result) == 5

    def test_list_with_item_filter(self, service, mock_db, org_id):
        """Test listing transactions with item filter."""
        item_id = uuid4()
        transactions = [
            MockInventoryTransaction(organization_id=org_id, item_id=item_id)
        ]
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = transactions

        result = service.list(mock_db, str(org_id), item_id=str(item_id))

        assert len(result) == 1

    def test_list_with_type_filter(self, service, mock_db, org_id):
        """Test listing transactions with type filter."""
        transactions = [
            MockInventoryTransaction(
                organization_id=org_id,
                transaction_type="RECEIPT",
            )
        ]
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = transactions

        result = service.list(
            mock_db, str(org_id), transaction_type=TransactionType.RECEIPT
        )

        assert len(result) == 1
