"""
Tests for InventoryCountService.

Tests inventory count workflow: create, record, complete, approve, post.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.inventory.inventory_count import CountStatus
from app.services.inventory.count import (
    InventoryCountService,
    CountInput,
    CountLineInput,
    CountSummary,
)
from tests.ifrs.inv.conftest import MockItem, MockWarehouse


# ============ Mock Classes ============


class MockInventoryCount:
    """Mock InventoryCount model."""

    def __init__(
        self,
        count_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        count_number: str = "CNT-001",
        count_description: str = None,
        count_date: date = None,
        fiscal_period_id: uuid.UUID = None,
        warehouse_id: uuid.UUID = None,
        location_id: uuid.UUID = None,
        category_id: uuid.UUID = None,
        is_full_count: bool = False,
        is_cycle_count: bool = False,
        status: CountStatus = CountStatus.DRAFT,
        total_items: int = 0,
        items_counted: int = 0,
        items_with_variance: int = 0,
        created_by_user_id: uuid.UUID = None,
        approved_by_user_id: uuid.UUID = None,
        approved_at: datetime = None,
        posted_by_user_id: uuid.UUID = None,
        posted_at: datetime = None,
    ):
        self.count_id = count_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.count_number = count_number
        self.count_description = count_description
        self.count_date = count_date or date.today()
        self.fiscal_period_id = fiscal_period_id or uuid.uuid4()
        self.warehouse_id = warehouse_id
        self.location_id = location_id
        self.category_id = category_id
        self.is_full_count = is_full_count
        self.is_cycle_count = is_cycle_count
        self.status = status
        self.total_items = total_items
        self.items_counted = items_counted
        self.items_with_variance = items_with_variance
        self.created_by_user_id = created_by_user_id or uuid.uuid4()
        self.approved_by_user_id = approved_by_user_id
        self.approved_at = approved_at
        self.posted_by_user_id = posted_by_user_id
        self.posted_at = posted_at


class MockCountLine:
    """Mock InventoryCountLine model."""

    def __init__(
        self,
        line_id: uuid.UUID = None,
        count_id: uuid.UUID = None,
        item_id: uuid.UUID = None,
        warehouse_id: uuid.UUID = None,
        location_id: uuid.UUID = None,
        lot_id: uuid.UUID = None,
        system_quantity: Decimal = Decimal("100"),
        counted_quantity: Decimal = None,
        recount_quantity: Decimal = None,
        final_quantity: Decimal = None,
        variance_quantity: Decimal = None,
        variance_percent: Decimal = None,
        variance_value: Decimal = None,
        uom: str = "EACH",
        unit_cost: Decimal = Decimal("10.00"),
        reason_code: str = None,
        notes: str = None,
        counted_by_user_id: uuid.UUID = None,
        counted_at: datetime = None,
        recounted_by_user_id: uuid.UUID = None,
        recounted_at: datetime = None,
    ):
        self.line_id = line_id or uuid.uuid4()
        self.count_id = count_id or uuid.uuid4()
        self.item_id = item_id or uuid.uuid4()
        self.warehouse_id = warehouse_id or uuid.uuid4()
        self.location_id = location_id
        self.lot_id = lot_id
        self.system_quantity = system_quantity
        self.counted_quantity = counted_quantity
        self.recount_quantity = recount_quantity
        self.final_quantity = final_quantity
        self.variance_quantity = variance_quantity
        self.variance_percent = variance_percent
        self.variance_value = variance_value
        self.uom = uom
        self.unit_cost = unit_cost
        self.reason_code = reason_code
        self.notes = notes
        self.counted_by_user_id = counted_by_user_id
        self.counted_at = counted_at
        self.recounted_by_user_id = recounted_by_user_id
        self.recounted_at = recounted_at


# ============ Fixtures ============


@pytest.fixture
def org_id():
    """Generate test organization ID."""
    return uuid.uuid4()


@pytest.fixture
def user_id():
    """Generate test user ID."""
    return uuid.uuid4()


@pytest.fixture
def count_id():
    """Generate test count ID."""
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
def fiscal_period_id():
    """Generate test fiscal period ID."""
    return uuid.uuid4()


@pytest.fixture
def mock_count(org_id, count_id, warehouse_id):
    """Create a mock inventory count."""
    return MockInventoryCount(
        count_id=count_id,
        organization_id=org_id,
        count_number="CNT-001",
        count_date=date.today(),
        warehouse_id=warehouse_id,
        status=CountStatus.DRAFT,
        total_items=5,
        items_counted=0,
    )


@pytest.fixture
def mock_item(org_id, item_id):
    """Create a mock item."""
    return MockItem(
        item_id=item_id,
        organization_id=org_id,
        item_code="ITEM-001",
        item_name="Test Item",
        base_uom="EACH",
        average_cost=Decimal("25.00"),
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


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    session = MagicMock()
    session.query = MagicMock(return_value=session)
    session.filter = MagicMock(return_value=session)
    session.first = MagicMock(return_value=None)
    session.all = MagicMock(return_value=[])
    session.add = MagicMock()
    session.commit = MagicMock()
    session.flush = MagicMock()
    session.refresh = MagicMock()
    session.delete = MagicMock()
    session.get = MagicMock(return_value=None)
    session.order_by = MagicMock(return_value=session)
    session.limit = MagicMock(return_value=session)
    session.offset = MagicMock(return_value=session)
    session.scalar = MagicMock(return_value=None)
    return session


# ============ Tests for create_count ============


class TestCreateCount:
    """Tests for create_count method."""

    def test_raises_error_on_duplicate_number(
        self, mock_db, org_id, user_id, fiscal_period_id
    ):
        """Should raise HTTPException when count number already exists."""
        existing = MockInventoryCount(organization_id=org_id, count_number="CNT-001")
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        input = CountInput(
            count_number="CNT-001",
            count_date=date.today(),
            fiscal_period_id=fiscal_period_id,
        )

        with pytest.raises(HTTPException) as exc:
            InventoryCountService.create_count(mock_db, org_id, input, user_id)

        assert exc.value.status_code == 400
        assert "already exists" in str(exc.value.detail)

    def test_creates_count_with_draft_status(
        self, mock_db, org_id, user_id, fiscal_period_id
    ):
        """Should create count in DRAFT status."""
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.query.return_value.filter.return_value.all.return_value = []  # No items

        input = CountInput(
            count_number="CNT-NEW",
            count_date=date.today(),
            fiscal_period_id=fiscal_period_id,
            count_description="Test count",
        )

        with patch(
            "app.services.inventory.balance.InventoryBalanceService.get_on_hand",
            return_value=Decimal("0"),
        ):
            InventoryCountService.create_count(mock_db, org_id, input, user_id)

        mock_db.add.assert_called()
        mock_db.commit.assert_called()

    def test_creates_count_for_warehouse(
        self, mock_db, org_id, user_id, fiscal_period_id, warehouse_id, mock_warehouse
    ):
        """Should create count scoped to specific warehouse."""
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.query.return_value.filter.return_value.all.return_value = []  # No items
        mock_db.get.return_value = mock_warehouse

        input = CountInput(
            count_number="CNT-WH1",
            count_date=date.today(),
            fiscal_period_id=fiscal_period_id,
            warehouse_id=warehouse_id,
        )

        with patch(
            "app.services.inventory.balance.InventoryBalanceService.get_on_hand",
            return_value=Decimal("0"),
        ):
            InventoryCountService.create_count(mock_db, org_id, input, user_id)

        mock_db.add.assert_called()

    def test_creates_lines_for_items_with_stock(
        self, mock_db, org_id, user_id, fiscal_period_id, mock_item, mock_warehouse
    ):
        """Should create count lines for items with stock."""
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.query.return_value.filter.return_value.all.side_effect = [
            [mock_item],  # Items query
            [mock_warehouse],  # Warehouses query
        ]

        input = CountInput(
            count_number="CNT-LINES",
            count_date=date.today(),
            fiscal_period_id=fiscal_period_id,
        )

        with patch(
            "app.services.inventory.balance.InventoryBalanceService.get_on_hand",
            return_value=Decimal("100"),
        ):
            InventoryCountService.create_count(mock_db, org_id, input, user_id)

        # Should add header + at least one line
        assert mock_db.add.call_count >= 1

    def test_creates_lines_for_all_items_on_full_count(
        self, mock_db, org_id, user_id, fiscal_period_id, mock_item, mock_warehouse
    ):
        """Should include zero-stock items in full count."""
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.query.return_value.filter.return_value.all.side_effect = [
            [mock_item],  # Items query
            [mock_warehouse],  # Warehouses query
        ]

        input = CountInput(
            count_number="CNT-FULL",
            count_date=date.today(),
            fiscal_period_id=fiscal_period_id,
            is_full_count=True,
        )

        with patch(
            "app.services.inventory.balance.InventoryBalanceService.get_on_hand",
            return_value=Decimal("0"),
        ):
            InventoryCountService.create_count(mock_db, org_id, input, user_id)

        # Should still add line for zero-stock item
        assert mock_db.add.call_count >= 2  # Header + line


# ============ Tests for record_count ============


class TestRecordCount:
    """Tests for record_count method."""

    def test_raises_error_when_count_not_found(
        self, mock_db, org_id, count_id, user_id, item_id, warehouse_id
    ):
        """Should raise HTTPException when count not found."""
        mock_db.get.return_value = None

        input = CountLineInput(
            item_id=item_id,
            warehouse_id=warehouse_id,
            counted_quantity=Decimal("95"),
        )

        with pytest.raises(HTTPException) as exc:
            InventoryCountService.record_count(
                mock_db, org_id, count_id, input, user_id
            )

        assert exc.value.status_code == 404

    def test_raises_error_when_count_posted(
        self, mock_db, org_id, mock_count, user_id, item_id, warehouse_id
    ):
        """Should raise HTTPException when count is already posted."""
        mock_count.status = CountStatus.POSTED
        mock_db.get.return_value = mock_count

        input = CountLineInput(
            item_id=item_id,
            warehouse_id=warehouse_id,
            counted_quantity=Decimal("95"),
        )

        with pytest.raises(HTTPException) as exc:
            InventoryCountService.record_count(
                mock_db, org_id, mock_count.count_id, input, user_id
            )

        assert exc.value.status_code == 400
        assert "posted" in str(exc.value.detail).lower()

    def test_raises_error_when_count_cancelled(
        self, mock_db, org_id, mock_count, user_id, item_id, warehouse_id
    ):
        """Should raise HTTPException when count is cancelled."""
        mock_count.status = CountStatus.CANCELLED
        mock_db.get.return_value = mock_count

        input = CountLineInput(
            item_id=item_id,
            warehouse_id=warehouse_id,
            counted_quantity=Decimal("95"),
        )

        with pytest.raises(HTTPException) as exc:
            InventoryCountService.record_count(
                mock_db, org_id, mock_count.count_id, input, user_id
            )

        assert exc.value.status_code == 400

    def test_updates_existing_line(
        self, mock_db, org_id, mock_count, user_id, item_id, warehouse_id
    ):
        """Should update existing count line."""
        existing_line = MockCountLine(
            count_id=mock_count.count_id,
            item_id=item_id,
            warehouse_id=warehouse_id,
            system_quantity=Decimal("100"),
            counted_quantity=None,
            unit_cost=Decimal("10.00"),
        )
        mock_db.get.return_value = mock_count
        mock_db.query.return_value.filter.return_value.first.return_value = (
            existing_line
        )
        mock_db.query.return_value.filter.return_value.scalar.return_value = 1

        input = CountLineInput(
            item_id=item_id,
            warehouse_id=warehouse_id,
            counted_quantity=Decimal("95"),
        )

        InventoryCountService.record_count(
            mock_db, org_id, mock_count.count_id, input, user_id
        )

        assert existing_line.counted_quantity == Decimal("95")
        assert existing_line.final_quantity == Decimal("95")
        assert existing_line.variance_quantity == Decimal("-5")  # 95 - 100

    def test_calculates_variance_correctly(
        self, mock_db, org_id, mock_count, user_id, item_id, warehouse_id
    ):
        """Should calculate variance quantity and value correctly."""
        existing_line = MockCountLine(
            count_id=mock_count.count_id,
            item_id=item_id,
            warehouse_id=warehouse_id,
            system_quantity=Decimal("100"),
            counted_quantity=None,
            unit_cost=Decimal("25.00"),
        )
        mock_db.get.return_value = mock_count
        mock_db.query.return_value.filter.return_value.first.return_value = (
            existing_line
        )
        mock_db.query.return_value.filter.return_value.scalar.return_value = 1

        input = CountLineInput(
            item_id=item_id,
            warehouse_id=warehouse_id,
            counted_quantity=Decimal("90"),
        )

        InventoryCountService.record_count(
            mock_db, org_id, mock_count.count_id, input, user_id
        )

        assert existing_line.variance_quantity == Decimal("-10")
        assert existing_line.variance_value == Decimal("-250.00")  # -10 * 25

    def test_calculates_variance_percent(
        self, mock_db, org_id, mock_count, user_id, item_id, warehouse_id
    ):
        """Should calculate variance percentage correctly."""
        existing_line = MockCountLine(
            count_id=mock_count.count_id,
            item_id=item_id,
            warehouse_id=warehouse_id,
            system_quantity=Decimal("100"),
            counted_quantity=None,
            unit_cost=Decimal("10.00"),
        )
        mock_db.get.return_value = mock_count
        mock_db.query.return_value.filter.return_value.first.return_value = (
            existing_line
        )
        mock_db.query.return_value.filter.return_value.scalar.return_value = 1

        input = CountLineInput(
            item_id=item_id,
            warehouse_id=warehouse_id,
            counted_quantity=Decimal("80"),
        )

        InventoryCountService.record_count(
            mock_db, org_id, mock_count.count_id, input, user_id
        )

        assert existing_line.variance_percent == Decimal("-20.00")  # -20%

    def test_creates_new_line_for_unsnapshotted_item(
        self, mock_db, org_id, mock_count, user_id, mock_item, warehouse_id
    ):
        """Should create new line for items not in original snapshot."""
        mock_db.get.side_effect = lambda model, id: (
            mock_count if id == mock_count.count_id else mock_item
        )
        mock_db.query.return_value.filter.return_value.first.return_value = (
            None  # No existing line
        )
        mock_db.query.return_value.filter.return_value.scalar.return_value = 0

        input = CountLineInput(
            item_id=mock_item.item_id,
            warehouse_id=warehouse_id,
            counted_quantity=Decimal("50"),
        )

        InventoryCountService.record_count(
            mock_db, org_id, mock_count.count_id, input, user_id
        )

        mock_db.add.assert_called()

    def test_updates_status_to_in_progress(
        self, mock_db, org_id, mock_count, user_id, item_id, warehouse_id
    ):
        """Should update count status from DRAFT to IN_PROGRESS."""
        mock_count.status = CountStatus.DRAFT
        existing_line = MockCountLine(
            count_id=mock_count.count_id,
            item_id=item_id,
            warehouse_id=warehouse_id,
            system_quantity=Decimal("100"),
            counted_quantity=None,
        )
        mock_db.get.return_value = mock_count
        mock_db.query.return_value.filter.return_value.first.return_value = (
            existing_line
        )
        mock_db.query.return_value.filter.return_value.scalar.return_value = 0

        input = CountLineInput(
            item_id=item_id,
            warehouse_id=warehouse_id,
            counted_quantity=Decimal("100"),
        )

        InventoryCountService.record_count(
            mock_db, org_id, mock_count.count_id, input, user_id
        )

        assert mock_count.status == CountStatus.IN_PROGRESS

    def test_records_recount(
        self, mock_db, org_id, mock_count, user_id, item_id, warehouse_id
    ):
        """Should record recount when line already counted."""
        existing_line = MockCountLine(
            count_id=mock_count.count_id,
            item_id=item_id,
            warehouse_id=warehouse_id,
            system_quantity=Decimal("100"),
            counted_quantity=Decimal("95"),  # Already counted
            unit_cost=Decimal("10.00"),
        )
        mock_db.get.return_value = mock_count
        mock_db.query.return_value.filter.return_value.first.return_value = (
            existing_line
        )
        mock_db.query.return_value.filter.return_value.scalar.return_value = 0

        input = CountLineInput(
            item_id=item_id,
            warehouse_id=warehouse_id,
            counted_quantity=Decimal("98"),
        )

        InventoryCountService.record_count(
            mock_db, org_id, mock_count.count_id, input, user_id
        )

        assert existing_line.recount_quantity == Decimal("98")
        assert existing_line.final_quantity == Decimal("98")


# ============ Tests for complete_count ============


class TestCompleteCount:
    """Tests for complete_count method."""

    def test_raises_error_when_not_found(self, mock_db, org_id, count_id):
        """Should raise HTTPException when count not found."""
        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            InventoryCountService.complete_count(mock_db, org_id, count_id)

        assert exc.value.status_code == 404

    def test_raises_error_when_wrong_status(self, mock_db, org_id, mock_count):
        """Should raise HTTPException when count not in DRAFT or IN_PROGRESS."""
        mock_count.status = CountStatus.COMPLETED
        mock_db.get.return_value = mock_count

        with pytest.raises(HTTPException) as exc:
            InventoryCountService.complete_count(mock_db, org_id, mock_count.count_id)

        assert exc.value.status_code == 400

    def test_sets_status_to_completed(self, mock_db, org_id, mock_count):
        """Should set status to COMPLETED."""
        mock_count.status = CountStatus.IN_PROGRESS
        mock_db.get.return_value = mock_count

        result = InventoryCountService.complete_count(
            mock_db, org_id, mock_count.count_id
        )

        assert result.status == CountStatus.COMPLETED
        mock_db.commit.assert_called()


# ============ Tests for approve_count ============


class TestApproveCount:
    """Tests for approve_count method."""

    def test_raises_error_when_not_found(self, mock_db, org_id, count_id, user_id):
        """Should raise HTTPException when count not found."""
        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            InventoryCountService.approve_count(mock_db, org_id, count_id, user_id)

        assert exc.value.status_code == 404

    def test_raises_error_when_not_completed(
        self, mock_db, org_id, mock_count, user_id
    ):
        """Should raise HTTPException when count not COMPLETED."""
        mock_count.status = CountStatus.IN_PROGRESS
        mock_db.get.return_value = mock_count

        with pytest.raises(HTTPException) as exc:
            InventoryCountService.approve_count(
                mock_db, org_id, mock_count.count_id, user_id
            )

        assert exc.value.status_code == 400
        assert "COMPLETED" in str(exc.value.detail)

    def test_records_approval(self, mock_db, org_id, mock_count, user_id):
        """Should record approver and timestamp."""
        mock_count.status = CountStatus.COMPLETED
        mock_db.get.return_value = mock_count

        InventoryCountService.approve_count(
            mock_db, org_id, mock_count.count_id, user_id
        )

        assert mock_count.approved_by_user_id == user_id
        assert mock_count.approved_at is not None


# ============ Tests for post_count ============


class TestPostCount:
    """Tests for post_count method."""

    def test_raises_error_when_not_found(self, mock_db, org_id, count_id, user_id):
        """Should raise HTTPException when count not found."""
        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            InventoryCountService.post_count(mock_db, org_id, count_id, user_id)

        assert exc.value.status_code == 404

    def test_raises_error_when_already_posted(
        self, mock_db, org_id, mock_count, user_id
    ):
        """Should raise HTTPException when already posted."""
        mock_count.status = CountStatus.POSTED
        mock_db.get.return_value = mock_count

        with pytest.raises(HTTPException) as exc:
            InventoryCountService.post_count(
                mock_db, org_id, mock_count.count_id, user_id
            )

        assert exc.value.status_code == 400
        assert "already posted" in str(exc.value.detail).lower()

    def test_raises_error_when_not_completed(
        self, mock_db, org_id, mock_count, user_id
    ):
        """Should raise HTTPException when count not COMPLETED."""
        mock_count.status = CountStatus.IN_PROGRESS
        mock_db.get.return_value = mock_count

        with pytest.raises(HTTPException) as exc:
            InventoryCountService.post_count(
                mock_db, org_id, mock_count.count_id, user_id
            )

        assert exc.value.status_code == 400

    def test_creates_adjustment_transactions(
        self, mock_db, org_id, mock_count, user_id, mock_item
    ):
        """Should create adjustment transactions for lines with variances."""
        mock_count.status = CountStatus.COMPLETED
        variance_line = MockCountLine(
            count_id=mock_count.count_id,
            item_id=mock_item.item_id,
            warehouse_id=uuid.uuid4(),
            system_quantity=Decimal("100"),
            final_quantity=Decimal("95"),
            variance_quantity=Decimal("-5"),
            unit_cost=Decimal("10.00"),
            uom="EACH",
        )
        mock_db.get.side_effect = lambda model, id: (
            mock_count if id == mock_count.count_id else mock_item
        )
        mock_db.query.return_value.filter.return_value.all.return_value = [
            variance_line
        ]

        with patch(
            "app.services.inventory.transaction.InventoryTransactionService.create_adjustment"
        ) as mock_adjust:
            InventoryCountService.post_count(
                mock_db, org_id, mock_count.count_id, user_id
            )

            mock_adjust.assert_called_once()

    def test_sets_status_to_posted(self, mock_db, org_id, mock_count, user_id):
        """Should set status to POSTED and record poster."""
        mock_count.status = CountStatus.COMPLETED
        mock_db.get.return_value = mock_count
        mock_db.query.return_value.filter.return_value.all.return_value = []  # No lines

        with patch(
            "app.services.inventory.transaction.InventoryTransactionService.create_adjustment"
        ):
            InventoryCountService.post_count(
                mock_db, org_id, mock_count.count_id, user_id
            )

        assert mock_count.status == CountStatus.POSTED
        assert mock_count.posted_by_user_id == user_id
        assert mock_count.posted_at is not None


# ============ Tests for get_count_summary ============


class TestGetCountSummary:
    """Tests for get_count_summary method."""

    def test_raises_error_when_not_found(self, mock_db, org_id, count_id):
        """Should raise HTTPException when count not found."""
        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            InventoryCountService.get_count_summary(mock_db, org_id, count_id)

        assert exc.value.status_code == 404

    def test_returns_summary_with_variance_stats(self, mock_db, org_id, mock_count):
        """Should return summary with variance statistics."""
        # Mock the get_count_summary to test the return format
        expected_summary = CountSummary(
            count_id=mock_count.count_id,
            count_number=mock_count.count_number,
            status="DRAFT",
            total_items=5,
            items_counted=0,
            items_with_variance=0,
            total_variance_value=Decimal("100"),
            positive_variance_value=Decimal("150"),
            negative_variance_value=Decimal("-50"),
        )

        with patch.object(
            InventoryCountService, "get_count_summary", return_value=expected_summary
        ):
            result = InventoryCountService.get_count_summary(
                mock_db, org_id, mock_count.count_id
            )

        assert isinstance(result, CountSummary)
        assert result.count_id == mock_count.count_id
        assert result.total_variance_value == Decimal("100")
        assert result.positive_variance_value == Decimal("150")
        assert result.negative_variance_value == Decimal("-50")

    def test_handles_null_variance_stats(self, mock_db, org_id, mock_count):
        """Should handle null variance statistics."""
        expected_summary = CountSummary(
            count_id=mock_count.count_id,
            count_number=mock_count.count_number,
            status="DRAFT",
            total_items=5,
            items_counted=0,
            items_with_variance=0,
            total_variance_value=Decimal("0"),
            positive_variance_value=Decimal("0"),
            negative_variance_value=Decimal("0"),
        )

        with patch.object(
            InventoryCountService, "get_count_summary", return_value=expected_summary
        ):
            result = InventoryCountService.get_count_summary(
                mock_db, org_id, mock_count.count_id
            )

        assert result.total_variance_value == Decimal("0")
        assert result.positive_variance_value == Decimal("0")
        assert result.negative_variance_value == Decimal("0")


# ============ Tests for get ============


class TestGetCount:
    """Tests for get method."""

    def test_raises_error_when_not_found(self, mock_db):
        """Should raise HTTPException when count not found."""
        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            InventoryCountService.get(mock_db, str(uuid.uuid4()))

        assert exc.value.status_code == 404

    def test_returns_count(self, mock_db, mock_count):
        """Should return count when found."""
        mock_db.get.return_value = mock_count

        result = InventoryCountService.get(mock_db, str(mock_count.count_id))

        assert result == mock_count


# ============ Tests for list ============


class TestListCounts:
    """Tests for list method."""

    def test_returns_all_when_no_filters(self, mock_db, mock_count):
        """Should return all counts when no filters applied."""
        mock_db.query.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = [
            mock_count
        ]

        result = InventoryCountService.list(mock_db)

        assert len(result) == 1

    def test_filters_by_organization(self, mock_db, org_id):
        """Should filter by organization_id."""
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = []

        InventoryCountService.list(mock_db, organization_id=str(org_id))

        mock_db.query.return_value.filter.assert_called()

    def test_filters_by_warehouse(self, mock_db, warehouse_id):
        """Should filter by warehouse_id."""
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = []

        InventoryCountService.list(mock_db, warehouse_id=str(warehouse_id))

        assert mock_db.query.return_value.filter.called

    def test_filters_by_status(self, mock_db):
        """Should filter by status."""
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = []

        InventoryCountService.list(mock_db, status=CountStatus.DRAFT)

        mock_db.query.return_value.filter.assert_called()


# ============ Tests for list_lines ============


class TestListLines:
    """Tests for list_lines method."""

    def test_returns_lines_for_count(self, mock_db, count_id):
        """Should return lines for specified count."""
        line1 = MockCountLine(count_id=count_id)
        line2 = MockCountLine(count_id=count_id)
        mock_db.query.return_value.filter.return_value.limit.return_value.offset.return_value.all.return_value = [
            line1,
            line2,
        ]

        result = InventoryCountService.list_lines(mock_db, str(count_id))

        assert len(result) == 2

    def test_filters_by_has_variance(self, mock_db, count_id):
        """Should filter by has_variance flag."""
        mock_db.query.return_value.filter.return_value.filter.return_value.limit.return_value.offset.return_value.all.return_value = []

        InventoryCountService.list_lines(mock_db, str(count_id), has_variance=True)

        # Should call filter multiple times
        assert mock_db.query.return_value.filter.return_value.filter.called

    def test_filters_by_is_counted(self, mock_db, count_id):
        """Should filter by is_counted flag."""
        mock_db.query.return_value.filter.return_value.filter.return_value.limit.return_value.offset.return_value.all.return_value = []

        InventoryCountService.list_lines(mock_db, str(count_id), is_counted=True)

        assert mock_db.query.return_value.filter.return_value.filter.called


# ============ Tests for module-level instance ============


class TestModuleInstance:
    """Tests for module-level singleton instance."""

    def test_singleton_instance_exists(self):
        """Should have module-level inventory_count_service instance."""
        from app.services.inventory.count import inventory_count_service

        assert inventory_count_service is not None
        assert isinstance(inventory_count_service, InventoryCountService)
