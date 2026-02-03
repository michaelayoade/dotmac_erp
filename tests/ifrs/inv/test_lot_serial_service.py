"""
Tests for LotSerialService.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.inventory.lot_serial import (
    LotSerialService,
    LotInput,
)
from tests.ifrs.inv.conftest import (
    MockItem,
    MockInventoryLot,
)


@pytest.fixture
def service():
    """Create LotSerialService instance."""
    return LotSerialService()


@pytest.fixture
def org_id():
    """Create test organization ID."""
    return uuid4()


@pytest.fixture
def sample_lot_input():
    """Create sample lot input."""
    return LotInput(
        item_id=uuid4(),
        lot_number="LOT-2024-001",
        received_date=date.today(),
        unit_cost=Decimal("10.00"),
        initial_quantity=Decimal("100"),
        manufacture_date=date.today(),
        expiry_date=date(2025, 12, 31),
    )


class TestCreateLot:
    """Tests for create_lot method."""

    def test_create_lot_success(self, service, mock_db, org_id, sample_lot_input):
        """Test successful lot creation."""
        item = MockItem(
            item_id=sample_lot_input.item_id,
            organization_id=org_id,
            track_lots=True,
        )

        # Mock item found, no existing lot
        mock_db.query.return_value.filter.return_value.first.side_effect = [
            item,  # Item found
            None,  # No existing lot
        ]

        result = service.create_lot(mock_db, org_id, sample_lot_input)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once()

    def test_create_lot_item_not_found(self, service, mock_db, org_id, sample_lot_input):
        """Test lot creation with invalid item."""
        from fastapi import HTTPException

        mock_db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.create_lot(mock_db, org_id, sample_lot_input)

        assert exc.value.status_code == 404
        assert "Item not found" in exc.value.detail

    def test_create_lot_item_not_lot_tracked(
        self, service, mock_db, org_id, sample_lot_input
    ):
        """Test lot creation for non-lot-tracked item."""
        from fastapi import HTTPException

        item = MockItem(
            item_id=sample_lot_input.item_id,
            organization_id=org_id,
            track_lots=False,
        )
        mock_db.query.return_value.filter.return_value.first.return_value = item

        with pytest.raises(HTTPException) as exc:
            service.create_lot(mock_db, org_id, sample_lot_input)

        assert exc.value.status_code == 400
        assert "not configured for lot tracking" in exc.value.detail

    def test_create_lot_duplicate_fails(
        self, service, mock_db, org_id, sample_lot_input
    ):
        """Test that duplicate lot number fails."""
        from fastapi import HTTPException

        item = MockItem(
            item_id=sample_lot_input.item_id,
            organization_id=org_id,
            track_lots=True,
        )
        existing_lot = MockInventoryLot(
            item_id=sample_lot_input.item_id,
            lot_number=sample_lot_input.lot_number,
        )

        mock_db.query.return_value.filter.return_value.first.side_effect = [
            item,
            existing_lot,
        ]

        with pytest.raises(HTTPException) as exc:
            service.create_lot(mock_db, org_id, sample_lot_input)

        assert exc.value.status_code == 400
        assert "already exists" in exc.value.detail


class TestAllocateFromLot:
    """Tests for allocate_from_lot method."""

    def test_allocate_success(self, service, mock_db):
        """Test successful lot allocation."""
        lot = MockInventoryLot(
            quantity_on_hand=Decimal("100"),
            quantity_available=Decimal("100"),
            quantity_allocated=Decimal("0"),
            is_quarantined=False,
        )
        mock_db.query.return_value.filter.return_value.first.return_value = lot

        result = service.allocate_from_lot(mock_db, lot.lot_id, Decimal("50"))

        mock_db.commit.assert_called_once()
        assert result.quantity_allocated == Decimal("50")
        assert result.quantity_available == Decimal("50")

    def test_allocate_lot_not_found(self, service, mock_db):
        """Test allocation from non-existent lot."""
        from fastapi import HTTPException

        mock_db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.allocate_from_lot(mock_db, uuid4(), Decimal("50"))

        assert exc.value.status_code == 404

    def test_allocate_quarantined_lot_fails(self, service, mock_db):
        """Test allocation from quarantined lot fails."""
        from fastapi import HTTPException

        lot = MockInventoryLot(
            quantity_available=Decimal("100"),
            is_quarantined=True,
        )
        mock_db.query.return_value.filter.return_value.first.return_value = lot

        with pytest.raises(HTTPException) as exc:
            service.allocate_from_lot(mock_db, lot.lot_id, Decimal("50"))

        assert exc.value.status_code == 400
        assert "quarantined" in exc.value.detail

    def test_allocate_insufficient_quantity_fails(self, service, mock_db):
        """Test allocation with insufficient quantity fails."""
        from fastapi import HTTPException

        lot = MockInventoryLot(
            quantity_available=Decimal("30"),
            is_quarantined=False,
        )
        mock_db.query.return_value.filter.return_value.first.return_value = lot

        with pytest.raises(HTTPException) as exc:
            service.allocate_from_lot(mock_db, lot.lot_id, Decimal("50"))

        assert exc.value.status_code == 400
        assert "Insufficient" in exc.value.detail


class TestDeallocateFromLot:
    """Tests for deallocate_from_lot method."""

    def test_deallocate_success(self, service, mock_db):
        """Test successful deallocation."""
        lot = MockInventoryLot(
            quantity_on_hand=Decimal("100"),
            quantity_allocated=Decimal("50"),
            quantity_available=Decimal("50"),
        )
        mock_db.query.return_value.filter.return_value.first.return_value = lot

        result = service.deallocate_from_lot(mock_db, lot.lot_id, Decimal("30"))

        mock_db.commit.assert_called_once()
        assert result.quantity_allocated == Decimal("20")
        assert result.quantity_available == Decimal("80")

    def test_deallocate_lot_not_found(self, service, mock_db):
        """Test deallocation from non-existent lot."""
        from fastapi import HTTPException

        mock_db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.deallocate_from_lot(mock_db, uuid4(), Decimal("30"))

        assert exc.value.status_code == 404


class TestConsumeFromLot:
    """Tests for consume_from_lot method."""

    def test_consume_success(self, service, mock_db):
        """Test successful consumption."""
        lot = MockInventoryLot(
            quantity_on_hand=Decimal("100"),
            quantity_allocated=Decimal("20"),
            quantity_available=Decimal("80"),
            is_active=True,
        )
        mock_db.query.return_value.filter.return_value.first.return_value = lot

        result = service.consume_from_lot(mock_db, lot.lot_id, Decimal("50"))

        mock_db.commit.assert_called_once()
        assert result.quantity_on_hand == Decimal("50")

    def test_consume_insufficient_quantity_fails(self, service, mock_db):
        """Test consumption with insufficient quantity fails."""
        from fastapi import HTTPException

        lot = MockInventoryLot(
            quantity_on_hand=Decimal("30"),
        )
        mock_db.query.return_value.filter.return_value.first.return_value = lot

        with pytest.raises(HTTPException) as exc:
            service.consume_from_lot(mock_db, lot.lot_id, Decimal("50"))

        assert exc.value.status_code == 400
        assert "Cannot consume" in exc.value.detail

    def test_consume_deactivates_depleted_lot(self, service, mock_db):
        """Test that consuming all quantity deactivates the lot."""
        lot = MockInventoryLot(
            quantity_on_hand=Decimal("50"),
            quantity_allocated=Decimal("0"),
            quantity_available=Decimal("50"),
            is_active=True,
        )
        mock_db.query.return_value.filter.return_value.first.return_value = lot

        result = service.consume_from_lot(mock_db, lot.lot_id, Decimal("50"))

        assert result.is_active is False


class TestQuarantineLot:
    """Tests for quarantine_lot method."""

    def test_quarantine_success(self, service, mock_db):
        """Test successful quarantine."""
        lot = MockInventoryLot(
            quantity_on_hand=Decimal("100"),
            quantity_available=Decimal("100"),
            is_quarantined=False,
        )
        mock_db.query.return_value.filter.return_value.first.return_value = lot

        result = service.quarantine_lot(mock_db, lot.lot_id, "Quality issue")

        mock_db.commit.assert_called_once()
        assert result.is_quarantined is True
        assert result.quarantine_reason == "Quality issue"
        assert result.quantity_available == Decimal("0")

    def test_quarantine_lot_not_found(self, service, mock_db):
        """Test quarantine of non-existent lot."""
        from fastapi import HTTPException

        mock_db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.quarantine_lot(mock_db, uuid4(), "Quality issue")

        assert exc.value.status_code == 404


class TestReleaseQuarantine:
    """Tests for release_quarantine method."""

    def test_release_quarantine_success(self, service, mock_db):
        """Test successful quarantine release."""
        lot = MockInventoryLot(
            quantity_on_hand=Decimal("100"),
            quantity_allocated=Decimal("0"),
            quantity_available=Decimal("0"),
            is_quarantined=True,
            quarantine_reason="Under review",
        )
        mock_db.query.return_value.filter.return_value.first.return_value = lot

        result = service.release_quarantine(mock_db, lot.lot_id, "PASSED")

        mock_db.commit.assert_called_once()
        assert result.is_quarantined is False
        assert result.quarantine_reason is None
        assert result.qc_status == "PASSED"
        assert result.quantity_available == Decimal("100")

    def test_release_quarantine_lot_not_found(self, service, mock_db):
        """Test releasing quarantine on non-existent lot."""
        from fastapi import HTTPException

        mock_db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.release_quarantine(mock_db, uuid4())

        assert exc.value.status_code == 404


class TestGetTraceability:
    """Tests for get_traceability method."""

    def test_get_traceability_success(self, service, mock_db):
        """Test successful traceability retrieval."""
        item = MockItem(item_code="ITEM-001")
        lot = MockInventoryLot(
            item_id=item.item_id,
            lot_number="LOT-001",
            initial_quantity=Decimal("100"),
            quantity_on_hand=Decimal("75"),
            received_date=date.today(),
        )

        mock_db.query.return_value.filter.return_value.first.side_effect = [lot, item]

        result = service.get_traceability(mock_db, lot.lot_id)

        assert result.lot_number == "LOT-001"
        assert result.total_received == Decimal("100")
        assert result.total_remaining == Decimal("75")
        assert result.total_consumed == Decimal("25")

    def test_get_traceability_lot_not_found(self, service, mock_db):
        """Test traceability for non-existent lot."""
        from fastapi import HTTPException

        mock_db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc:
            service.get_traceability(mock_db, uuid4())

        assert exc.value.status_code == 404


class TestGetLot:
    """Tests for get method."""

    def test_get_existing_lot(self, service, mock_db):
        """Test getting existing lot."""
        lot = MockInventoryLot()
        mock_db.query.return_value.filter.return_value.first.return_value = lot

        result = service.get(mock_db, str(lot.lot_id))

        assert result == lot

    def test_get_nonexistent_lot(self, service, mock_db):
        """Test getting non-existent lot."""
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = service.get(mock_db, str(uuid4()))

        assert result is None


class TestGetByNumber:
    """Tests for get_by_number method."""

    def test_get_by_number_success(self, service, mock_db):
        """Test getting lot by number."""
        lot = MockInventoryLot(lot_number="LOT-001")
        mock_db.query.return_value.filter.return_value.first.return_value = lot

        result = service.get_by_number(mock_db, lot.item_id, "LOT-001")

        assert result == lot

    def test_get_by_number_not_found(self, service, mock_db):
        """Test getting non-existent lot by number."""
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = service.get_by_number(mock_db, uuid4(), "NOTFOUND")

        assert result is None


class TestListLots:
    """Tests for list methods."""

    def test_list_by_item(self, service, mock_db):
        """Test listing lots by item."""
        item_id = uuid4()
        lots = [MockInventoryLot(item_id=item_id) for _ in range(3)]
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.all.return_value = (
            lots
        )

        result = service.list_by_item(mock_db, item_id)

        assert len(result) == 3

    def test_list_with_filters(self, service, mock_db, org_id):
        """Test listing lots with filters."""
        lots = [MockInventoryLot() for _ in range(2)]
        # Build the mock chain properly - query returns a mock that has all chained methods
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.join.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = lots

        result = service.list(
            mock_db,
            organization_id=str(org_id),
            is_quarantined=False,
            has_expiry=True,
        )

        assert len(result) == 2
