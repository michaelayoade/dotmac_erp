"""
Tests for PriceListService.

Tests price list management and price resolution logic.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.models.inventory.price_list import PriceListType
from app.services.inventory.price_list import (
    PriceListInput,
    PriceListItemInput,
    PriceListService,
    ResolvedPrice,
)
from tests.ifrs.inv.conftest import MockItem

# ============ Mock Classes ============


class MockPriceList:
    """Mock PriceList model."""

    def __init__(
        self,
        price_list_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        price_list_code: str = "PL-001",
        price_list_name: str = "Standard Sales",
        description: str = None,
        price_list_type: PriceListType = PriceListType.SALES,
        currency_code: str = "USD",
        effective_from: date = None,
        effective_to: date = None,
        priority: int = 0,
        base_price_list_id: uuid.UUID = None,
        markup_percent: Decimal = None,
        is_default: bool = False,
        is_active: bool = True,
    ):
        self.price_list_id = price_list_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.price_list_code = price_list_code
        self.price_list_name = price_list_name
        self.description = description
        self.price_list_type = price_list_type
        self.currency_code = currency_code
        self.effective_from = effective_from
        self.effective_to = effective_to
        self.priority = priority
        self.base_price_list_id = base_price_list_id
        self.markup_percent = markup_percent
        self.is_default = is_default
        self.is_active = is_active


class MockPriceListItem:
    """Mock PriceListItem model."""

    def __init__(
        self,
        price_list_item_id: uuid.UUID = None,
        price_list_id: uuid.UUID = None,
        item_id: uuid.UUID = None,
        unit_price: Decimal = Decimal("100.00"),
        currency_code: str = "USD",
        min_quantity: Decimal = Decimal("1"),
        discount_percent: Decimal = None,
        discount_amount: Decimal = None,
        effective_from: date = None,
        effective_to: date = None,
        is_active: bool = True,
    ):
        self.price_list_item_id = price_list_item_id or uuid.uuid4()
        self.price_list_id = price_list_id or uuid.uuid4()
        self.item_id = item_id or uuid.uuid4()
        self.unit_price = unit_price
        self.currency_code = currency_code
        self.min_quantity = min_quantity
        self.discount_percent = discount_percent
        self.discount_amount = discount_amount
        self.effective_from = effective_from
        self.effective_to = effective_to
        self.is_active = is_active


# ============ Fixtures ============


@pytest.fixture
def org_id():
    """Generate test organization ID."""
    return uuid.uuid4()


@pytest.fixture
def item_id():
    """Generate test item ID."""
    return uuid.uuid4()


@pytest.fixture
def price_list_id():
    """Generate test price list ID."""
    return uuid.uuid4()


@pytest.fixture
def mock_item(org_id, item_id):
    """Create a mock item."""
    return MockItem(
        item_id=item_id,
        organization_id=org_id,
        item_code="ITEM-001",
        item_name="Test Item",
        list_price=Decimal("150.00"),
        average_cost=Decimal("75.00"),
        currency_code="USD",
    )


@pytest.fixture
def mock_price_list(org_id, price_list_id):
    """Create a mock price list."""
    return MockPriceList(
        price_list_id=price_list_id,
        organization_id=org_id,
        price_list_code="PL-SALES",
        price_list_name="Standard Sales Price List",
        price_list_type=PriceListType.SALES,
        currency_code="USD",
        is_default=True,
        is_active=True,
    )


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    session = MagicMock()
    session.scalars = MagicMock(
        return_value=MagicMock(
            first=MagicMock(return_value=None), all=MagicMock(return_value=[])
        )
    )
    session.scalar = MagicMock(return_value=None)
    session.execute = MagicMock()
    session.add = MagicMock()
    session.commit = MagicMock()
    session.flush = MagicMock()
    session.refresh = MagicMock()
    session.delete = MagicMock()
    session.get = MagicMock(return_value=None)
    return session


# ============ Tests for create_price_list ============


class TestCreatePriceList:
    """Tests for create_price_list method."""

    def test_raises_error_on_duplicate_code(self, mock_db, org_id):
        """Should raise HTTPException when price list code already exists."""
        existing = MockPriceList(organization_id=org_id, price_list_code="PL-001")
        mock_db.scalars.return_value.first.return_value = existing

        input = PriceListInput(
            price_list_code="PL-001",
            price_list_name="New List",
            price_list_type=PriceListType.SALES,
            currency_code="USD",
        )

        with pytest.raises(HTTPException) as exc:
            PriceListService.create_price_list(mock_db, org_id, input)

        assert exc.value.status_code == 400
        assert "already exists" in str(exc.value.detail)

    def test_creates_price_list_successfully(self, mock_db, org_id):
        """Should create price list when code is unique."""
        mock_db.scalars.return_value.first.return_value = None

        input = PriceListInput(
            price_list_code="PL-NEW",
            price_list_name="New Price List",
            price_list_type=PriceListType.SALES,
            currency_code="USD",
            description="Test description",
            priority=10,
        )

        PriceListService.create_price_list(mock_db, org_id, input)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once()

    def test_clears_other_defaults_when_setting_default(self, mock_db, org_id):
        """Should clear other default price lists when setting new default."""
        mock_db.scalars.return_value.first.return_value = None

        input = PriceListInput(
            price_list_code="PL-NEW",
            price_list_name="New Default List",
            price_list_type=PriceListType.SALES,
            currency_code="USD",
            is_default=True,
        )

        PriceListService.create_price_list(mock_db, org_id, input)

        # Should have called update to clear other defaults
        assert mock_db.execute.called

    def test_creates_with_effective_dates(self, mock_db, org_id):
        """Should create price list with effective date range."""
        mock_db.scalars.return_value.first.return_value = None

        today = date.today()
        input = PriceListInput(
            price_list_code="PL-PROMO",
            price_list_name="Promotional Prices",
            price_list_type=PriceListType.SALES,
            currency_code="USD",
            effective_from=today,
            effective_to=today + timedelta(days=30),
        )

        PriceListService.create_price_list(mock_db, org_id, input)

        mock_db.add.assert_called_once()

    def test_creates_with_base_price_list(self, mock_db, org_id):
        """Should create price list inheriting from base with markup."""
        base_pl_id = uuid.uuid4()
        mock_db.scalars.return_value.first.return_value = None

        input = PriceListInput(
            price_list_code="PL-RETAIL",
            price_list_name="Retail Prices",
            price_list_type=PriceListType.SALES,
            currency_code="USD",
            base_price_list_id=base_pl_id,
            markup_percent=Decimal("25.0"),
        )

        PriceListService.create_price_list(mock_db, org_id, input)

        mock_db.add.assert_called_once()


# ============ Tests for update_price_list ============


class TestUpdatePriceList:
    """Tests for update_price_list method."""

    def test_raises_error_when_not_found(self, mock_db, org_id, price_list_id):
        """Should raise HTTPException when price list not found."""
        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            PriceListService.update_price_list(
                mock_db, org_id, price_list_id, {"priority": 5}
            )

        assert exc.value.status_code == 404

    def test_raises_error_when_different_org(self, mock_db, org_id, mock_price_list):
        """Should raise HTTPException when price list belongs to different org."""
        mock_price_list.organization_id = uuid.uuid4()  # Different org
        mock_db.get.return_value = mock_price_list

        with pytest.raises(HTTPException) as exc:
            PriceListService.update_price_list(
                mock_db, org_id, mock_price_list.price_list_id, {"priority": 5}
            )

        assert exc.value.status_code == 404

    def test_updates_fields_successfully(self, mock_db, org_id, mock_price_list):
        """Should update allowed fields successfully."""
        mock_db.get.return_value = mock_price_list

        PriceListService.update_price_list(
            mock_db,
            org_id,
            mock_price_list.price_list_id,
            {"price_list_name": "Updated Name", "priority": 10},
        )

        mock_db.commit.assert_called()
        assert mock_price_list.price_list_name == "Updated Name"
        assert mock_price_list.priority == 10

    def test_clears_defaults_when_setting_new_default(
        self, mock_db, org_id, mock_price_list
    ):
        """Should clear other defaults when setting as default."""
        mock_price_list.is_default = False
        mock_db.get.return_value = mock_price_list

        PriceListService.update_price_list(
            mock_db, org_id, mock_price_list.price_list_id, {"is_default": True}
        )

        mock_db.execute.assert_called()


# ============ Tests for add_item_price ============


class TestAddItemPrice:
    """Tests for add_item_price method."""

    def test_raises_error_when_price_list_not_found(
        self, mock_db, org_id, price_list_id, item_id
    ):
        """Should raise HTTPException when price list not found."""
        mock_db.get.return_value = None

        input = PriceListItemInput(
            item_id=item_id,
            unit_price=Decimal("100.00"),
            currency_code="USD",
        )

        with pytest.raises(HTTPException) as exc:
            PriceListService.add_item_price(mock_db, org_id, price_list_id, input)

        assert exc.value.status_code == 404
        assert "Price list not found" in str(exc.value.detail)

    def test_raises_error_when_item_not_found(
        self, mock_db, org_id, mock_price_list, item_id
    ):
        """Should raise HTTPException when item not found."""
        mock_db.get.side_effect = lambda model, id: (
            mock_price_list if id == mock_price_list.price_list_id else None
        )

        input = PriceListItemInput(
            item_id=item_id,
            unit_price=Decimal("100.00"),
            currency_code="USD",
        )

        with pytest.raises(HTTPException) as exc:
            PriceListService.add_item_price(
                mock_db, org_id, mock_price_list.price_list_id, input
            )

        assert exc.value.status_code == 404
        assert "Item not found" in str(exc.value.detail)

    def test_creates_new_item_price(self, mock_db, org_id, mock_price_list, mock_item):
        """Should create new item price when none exists."""
        mock_db.get.side_effect = lambda model, id: (
            mock_price_list if id == mock_price_list.price_list_id else mock_item
        )
        mock_db.scalars.return_value.first.return_value = None

        input = PriceListItemInput(
            item_id=mock_item.item_id,
            unit_price=Decimal("125.00"),
            currency_code="USD",
            min_quantity=Decimal("1"),
        )

        PriceListService.add_item_price(
            mock_db, org_id, mock_price_list.price_list_id, input
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called()

    def test_updates_existing_item_price(
        self, mock_db, org_id, mock_price_list, mock_item
    ):
        """Should update existing item price with same quantity break."""
        existing_item = MockPriceListItem(
            price_list_id=mock_price_list.price_list_id,
            item_id=mock_item.item_id,
            unit_price=Decimal("100.00"),
            min_quantity=Decimal("1"),
        )
        mock_db.get.side_effect = lambda model, id: (
            mock_price_list if id == mock_price_list.price_list_id else mock_item
        )
        mock_db.scalars.return_value.first.return_value = existing_item

        input = PriceListItemInput(
            item_id=mock_item.item_id,
            unit_price=Decimal("125.00"),
            currency_code="USD",
            min_quantity=Decimal("1"),
            discount_percent=Decimal("10"),
        )

        PriceListService.add_item_price(
            mock_db, org_id, mock_price_list.price_list_id, input
        )

        assert existing_item.unit_price == Decimal("125.00")
        assert existing_item.discount_percent == Decimal("10")
        mock_db.add.assert_not_called()  # Should update existing, not add new

    def test_creates_quantity_break_pricing(
        self, mock_db, org_id, mock_price_list, mock_item
    ):
        """Should allow multiple prices for same item with different quantity breaks."""
        mock_db.get.side_effect = lambda model, id: (
            mock_price_list if id == mock_price_list.price_list_id else mock_item
        )
        mock_db.scalars.return_value.first.return_value = None

        # Quantity break for 10+
        input = PriceListItemInput(
            item_id=mock_item.item_id,
            unit_price=Decimal("90.00"),  # Lower price for bulk
            currency_code="USD",
            min_quantity=Decimal("10"),
        )

        PriceListService.add_item_price(
            mock_db, org_id, mock_price_list.price_list_id, input
        )

        mock_db.add.assert_called_once()


# ============ Tests for remove_item_price ============


class TestRemoveItemPrice:
    """Tests for remove_item_price method."""

    def test_raises_error_when_not_found(self, mock_db, org_id):
        """Should raise HTTPException when item not found."""
        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            PriceListService.remove_item_price(mock_db, org_id, uuid.uuid4())

        assert exc.value.status_code == 404

    def test_raises_error_when_wrong_organization(
        self, mock_db, org_id, mock_price_list
    ):
        """Should raise HTTPException when price list belongs to different org."""
        item_price = MockPriceListItem(price_list_id=mock_price_list.price_list_id)
        mock_price_list.organization_id = uuid.uuid4()  # Different org
        mock_db.get.side_effect = lambda model, id: (
            item_price if id == item_price.price_list_item_id else mock_price_list
        )

        with pytest.raises(HTTPException) as exc:
            PriceListService.remove_item_price(
                mock_db, org_id, item_price.price_list_item_id
            )

        assert exc.value.status_code == 404

    def test_deletes_item_price(self, mock_db, org_id, mock_price_list):
        """Should delete item price successfully."""
        item_price = MockPriceListItem(price_list_id=mock_price_list.price_list_id)
        mock_db.get.side_effect = lambda model, id: (
            item_price if id == item_price.price_list_item_id else mock_price_list
        )

        result = PriceListService.remove_item_price(
            mock_db, org_id, item_price.price_list_item_id
        )

        assert result is True
        mock_db.delete.assert_called_once_with(item_price)
        mock_db.commit.assert_called()


# ============ Tests for resolve_price ============


class TestResolvePrice:
    """Tests for resolve_price method."""

    def test_raises_error_when_item_not_found(self, mock_db, org_id, item_id):
        """Should raise HTTPException when item not found."""
        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            PriceListService.resolve_price(mock_db, org_id, item_id)

        assert exc.value.status_code == 404

    def test_resolves_from_price_list(
        self, mock_db, org_id, mock_item, mock_price_list
    ):
        """Should resolve price from price list when available."""
        item_price = MockPriceListItem(
            price_list_id=mock_price_list.price_list_id,
            item_id=mock_item.item_id,
            unit_price=Decimal("120.00"),
            min_quantity=Decimal("1"),
        )
        mock_db.get.return_value = mock_item
        mock_db.scalars.return_value.all.return_value = [mock_price_list]
        mock_db.scalars.return_value.first.return_value = item_price

        result = PriceListService.resolve_price(mock_db, org_id, mock_item.item_id)

        assert isinstance(result, ResolvedPrice)
        assert result.unit_price == Decimal("120.00")
        assert result.source == "PRICE_LIST"
        assert result.price_list_id == mock_price_list.price_list_id

    def test_applies_discount_percent(
        self, mock_db, org_id, mock_item, mock_price_list
    ):
        """Should apply discount percentage to calculate net price."""
        item_price = MockPriceListItem(
            price_list_id=mock_price_list.price_list_id,
            item_id=mock_item.item_id,
            unit_price=Decimal("100.00"),
            discount_percent=Decimal("10"),  # 10% off
            min_quantity=Decimal("1"),
        )
        mock_db.get.return_value = mock_item
        mock_db.scalars.return_value.all.return_value = [mock_price_list]
        mock_db.scalars.return_value.first.return_value = item_price

        result = PriceListService.resolve_price(mock_db, org_id, mock_item.item_id)

        assert result.unit_price == Decimal("100.00")
        assert result.discount_percent == Decimal("10")
        assert result.net_price == Decimal("90.00")  # 100 * 0.9

    def test_applies_discount_amount(self, mock_db, org_id, mock_item, mock_price_list):
        """Should apply discount amount to calculate net price."""
        item_price = MockPriceListItem(
            price_list_id=mock_price_list.price_list_id,
            item_id=mock_item.item_id,
            unit_price=Decimal("100.00"),
            discount_amount=Decimal("15.00"),  # $15 off
            min_quantity=Decimal("1"),
        )
        mock_db.get.return_value = mock_item
        mock_db.scalars.return_value.all.return_value = [mock_price_list]
        mock_db.scalars.return_value.first.return_value = item_price

        result = PriceListService.resolve_price(mock_db, org_id, mock_item.item_id)

        assert result.net_price == Decimal("85.00")  # 100 - 15

    def test_applies_both_discounts(self, mock_db, org_id, mock_item, mock_price_list):
        """Should apply both discount percent and amount."""
        item_price = MockPriceListItem(
            price_list_id=mock_price_list.price_list_id,
            item_id=mock_item.item_id,
            unit_price=Decimal("100.00"),
            discount_percent=Decimal("10"),  # 10% off first
            discount_amount=Decimal("5.00"),  # Then $5 off
            min_quantity=Decimal("1"),
        )
        mock_db.get.return_value = mock_item
        mock_db.scalars.return_value.all.return_value = [mock_price_list]
        mock_db.scalars.return_value.first.return_value = item_price

        result = PriceListService.resolve_price(mock_db, org_id, mock_item.item_id)

        # 100 * 0.9 = 90, then 90 - 5 = 85
        assert result.net_price == Decimal("85.00")

    def test_resolves_quantity_break(self, mock_db, org_id, mock_item, mock_price_list):
        """Should resolve correct quantity break price."""
        bulk_price = MockPriceListItem(
            price_list_id=mock_price_list.price_list_id,
            item_id=mock_item.item_id,
            unit_price=Decimal("80.00"),  # Lower price for bulk
            min_quantity=Decimal("10"),
        )
        mock_db.get.return_value = mock_item
        mock_db.scalars.return_value.all.return_value = [mock_price_list]
        mock_db.scalars.return_value.first.return_value = bulk_price

        result = PriceListService.resolve_price(
            mock_db, org_id, mock_item.item_id, quantity=Decimal("15")
        )

        assert result.unit_price == Decimal("80.00")
        assert result.quantity_break == Decimal("10")

    def test_fallback_to_list_price(self, mock_db, org_id, mock_item):
        """Should fall back to item's list_price when no price list match."""
        mock_item.list_price = Decimal("150.00")
        mock_db.get.return_value = mock_item
        mock_db.scalars.return_value.all.return_value = []

        result = PriceListService.resolve_price(mock_db, org_id, mock_item.item_id)

        assert result.source == "ITEM_LIST_PRICE"
        assert result.unit_price == Decimal("150.00")
        assert result.price_list_id is None

    def test_fallback_to_average_cost(self, mock_db, org_id, mock_item):
        """Should fall back to average_cost when no list_price."""
        mock_item.list_price = None
        mock_item.average_cost = Decimal("75.00")
        mock_db.get.return_value = mock_item
        mock_db.scalars.return_value.all.return_value = []

        result = PriceListService.resolve_price(mock_db, org_id, mock_item.item_id)

        assert result.source == "ITEM_AVERAGE_COST"
        assert result.unit_price == Decimal("75.00")

    def test_returns_zero_when_no_pricing(self, mock_db, org_id, mock_item):
        """Should return zero when no pricing available."""
        mock_item.list_price = None
        mock_item.average_cost = None
        mock_db.get.return_value = mock_item
        mock_db.scalars.return_value.all.return_value = []

        result = PriceListService.resolve_price(mock_db, org_id, mock_item.item_id)

        assert result.source == "ITEM_AVERAGE_COST"
        assert result.unit_price == Decimal("0")

    def test_filters_by_specific_price_list(
        self, mock_db, org_id, mock_item, mock_price_list
    ):
        """Should filter to specific price list when ID provided."""
        item_price = MockPriceListItem(
            price_list_id=mock_price_list.price_list_id,
            item_id=mock_item.item_id,
            unit_price=Decimal("95.00"),
            min_quantity=Decimal("1"),
        )
        mock_db.get.return_value = mock_item
        mock_db.scalars.return_value.all.return_value = [mock_price_list]
        mock_db.scalars.return_value.first.return_value = item_price

        result = PriceListService.resolve_price(
            mock_db,
            org_id,
            mock_item.item_id,
            price_list_id=mock_price_list.price_list_id,
        )

        assert result.price_list_id == mock_price_list.price_list_id

    def test_respects_effective_date(self, mock_db, org_id, mock_item, mock_price_list):
        """Should respect as_of_date for price list effective dates."""
        mock_db.get.return_value = mock_item
        mock_db.scalars.return_value.all.return_value = []  # No active price lists

        # Price list not yet effective
        result = PriceListService.resolve_price(
            mock_db,
            org_id,
            mock_item.item_id,
            as_of_date=date.today() - timedelta(days=30),
        )

        # Should fall back to list price
        assert result.source == "ITEM_LIST_PRICE"

    def test_applies_markup_from_base_price_list(self, mock_db, org_id, mock_item):
        """Should apply markup percent from base price list."""
        base_pl_id = uuid.uuid4()
        derived_pl = MockPriceList(
            organization_id=org_id,
            price_list_code="PL-DERIVED",
            base_price_list_id=base_pl_id,
            markup_percent=Decimal("25"),  # 25% markup
            is_active=True,
        )

        base_item_price = MockPriceListItem(
            price_list_id=base_pl_id,
            item_id=mock_item.item_id,
            unit_price=Decimal("100.00"),
            min_quantity=Decimal("1"),
        )

        mock_db.get.return_value = mock_item
        mock_db.scalars.return_value.all.return_value = [derived_pl]
        # First call returns None (no direct price), then returns base price
        mock_db.scalars.return_value.first.side_effect = [
            None,  # No direct price in derived list
            base_item_price,  # Base price list lookup
        ]

        result = PriceListService.resolve_price(mock_db, org_id, mock_item.item_id)

        # 100 * 1.25 = 125
        assert result.unit_price == Decimal("125.00")
        assert result.source == "PRICE_LIST"


# ============ Tests for get ============


class TestGetPriceList:
    """Tests for get method."""

    def test_raises_error_when_not_found(self, mock_db):
        """Should raise HTTPException when price list not found."""
        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            PriceListService.get(mock_db, str(uuid.uuid4()))

        assert exc.value.status_code == 404

    def test_returns_price_list(self, mock_db, mock_price_list):
        """Should return price list when found."""
        mock_db.get.return_value = mock_price_list

        result = PriceListService.get(mock_db, str(mock_price_list.price_list_id))

        assert result == mock_price_list


# ============ Tests for list ============


class TestListPriceLists:
    """Tests for list method."""

    def test_returns_all_when_no_filters(self, mock_db, mock_price_list):
        """Should return all price lists when no filters applied."""
        mock_db.scalars.return_value.all.return_value = [mock_price_list]

        result = PriceListService.list(mock_db)

        assert len(result) == 1

    def test_filters_by_organization(self, mock_db, org_id):
        """Should filter by organization_id."""
        mock_db.scalars.return_value.all.return_value = []

        PriceListService.list(mock_db, organization_id=str(org_id))

        mock_db.scalars.assert_called()

    def test_filters_by_type(self, mock_db):
        """Should filter by price_list_type."""
        mock_db.scalars.return_value.all.return_value = []

        PriceListService.list(mock_db, price_list_type=PriceListType.PURCHASE)

        mock_db.scalars.assert_called()

    def test_filters_by_active_status(self, mock_db):
        """Should filter by is_active."""
        mock_db.scalars.return_value.all.return_value = []

        PriceListService.list(mock_db, is_active=True)

        mock_db.scalars.assert_called()

    def test_filters_by_currency(self, mock_db):
        """Should filter by currency_code."""
        mock_db.scalars.return_value.all.return_value = []

        PriceListService.list(mock_db, currency_code="EUR")

        mock_db.scalars.assert_called()

    def test_applies_pagination(self, mock_db):
        """Should apply limit and offset."""
        mock_db.scalars.return_value.all.return_value = []

        PriceListService.list(mock_db, limit=25, offset=50)

        mock_db.scalars.assert_called()


# ============ Tests for list_items ============


class TestListItems:
    """Tests for list_items method."""

    def test_returns_items_for_price_list(self, mock_db, price_list_id):
        """Should return items for specified price list."""
        item1 = MockPriceListItem(
            price_list_id=price_list_id, min_quantity=Decimal("1")
        )
        item2 = MockPriceListItem(
            price_list_id=price_list_id, min_quantity=Decimal("10")
        )
        mock_db.scalars.return_value.all.return_value = [item1, item2]

        result = PriceListService.list_items(mock_db, str(price_list_id))

        assert len(result) == 2

    def test_filters_by_item_id(self, mock_db, price_list_id, item_id):
        """Should filter by item_id when provided."""
        mock_db.scalars.return_value.all.return_value = []

        PriceListService.list_items(mock_db, str(price_list_id), item_id=str(item_id))

        mock_db.scalars.assert_called()

    def test_applies_pagination(self, mock_db, price_list_id):
        """Should apply limit and offset."""
        mock_db.scalars.return_value.all.return_value = []

        PriceListService.list_items(mock_db, str(price_list_id), limit=25, offset=10)

        mock_db.scalars.assert_called()


# ============ Tests for module-level instance ============


class TestModuleInstance:
    """Tests for module-level singleton instance."""

    def test_singleton_instance_exists(self):
        """Should have module-level price_list_service instance."""
        from app.services.inventory.price_list import price_list_service

        assert price_list_service is not None
        assert isinstance(price_list_service, PriceListService)
