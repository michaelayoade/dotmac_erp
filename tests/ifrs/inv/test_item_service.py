"""
Tests for ItemService and ItemCategoryService.
"""

from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.services.inventory.item import (
    ItemService,
    ItemCategoryService,
    ItemInput,
    ItemCategoryInput,
)
from app.models.inventory.item import ItemType, CostingMethod
from tests.ifrs.inv.conftest import (
    MockItem,
    MockItemCategory,
    MockItemType,
    MockCostingMethod,
)


@pytest.fixture
def item_service():
    """Create ItemService instance."""
    return ItemService()


@pytest.fixture
def category_service():
    """Create ItemCategoryService instance."""
    return ItemCategoryService()


@pytest.fixture
def org_id():
    """Create test organization ID."""
    return uuid4()


@pytest.fixture
def user_id():
    """Create test user ID."""
    return uuid4()


@pytest.fixture
def sample_category_input():
    """Create sample category input."""
    return ItemCategoryInput(
        category_code="CAT-001",
        category_name="General Inventory",
        inventory_account_id=uuid4(),
        cogs_account_id=uuid4(),
        revenue_account_id=uuid4(),
        inventory_adjustment_account_id=uuid4(),
        description="General inventory items",
    )


@pytest.fixture
def sample_item_input():
    """Create sample item input."""
    return ItemInput(
        item_code="ITEM-001",
        item_name="Test Item",
        category_id=uuid4(),
        base_uom="EACH",
        currency_code="USD",
        item_type=ItemType.INVENTORY,
        costing_method=CostingMethod.WEIGHTED_AVERAGE,
        description="A test inventory item",
    )


# ============ ItemCategoryService Tests ============


class TestCreateCategory:
    """Tests for ItemCategoryService.create_category method."""

    def test_create_category_success(
        self, category_service, mock_db, org_id, sample_category_input
    ):
        """Test successful category creation."""
        # No existing category
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = category_service.create_category(
            mock_db, org_id, sample_category_input
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once()

    def test_create_category_duplicate_fails(
        self, category_service, mock_db, org_id, sample_category_input
    ):
        """Test that duplicate category code fails."""
        from fastapi import HTTPException

        # Existing category found
        existing = MockItemCategory(
            organization_id=org_id,
            category_code=sample_category_input.category_code,
        )
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        with pytest.raises(HTTPException) as exc:
            category_service.create_category(mock_db, org_id, sample_category_input)

        assert exc.value.status_code == 400
        assert "already exists" in exc.value.detail


class TestGetCategory:
    """Tests for ItemCategoryService.get method."""

    def test_get_existing_category(self, category_service, mock_db, org_id):
        """Test getting existing category."""
        category = MockItemCategory(organization_id=org_id)
        mock_db.get.return_value = category

        result = category_service.get(mock_db, str(category.category_id))

        assert result == category

    def test_get_nonexistent_category_fails(self, category_service, mock_db):
        """Test getting non-existent category raises 404."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            category_service.get(mock_db, str(uuid4()))

        assert exc.value.status_code == 404


class TestListCategories:
    """Tests for ItemCategoryService.list method."""

    def test_list_all_categories(self, category_service, mock_db, org_id):
        """Test listing all categories."""
        categories = [MockItemCategory(organization_id=org_id) for _ in range(3)]
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = (
            categories
        )

        result = category_service.list(mock_db, str(org_id))

        assert len(result) == 3

    def test_list_active_categories_only(self, category_service, mock_db, org_id):
        """Test listing only active categories."""
        categories = [MockItemCategory(organization_id=org_id, is_active=True)]
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = (
            categories
        )

        result = category_service.list(mock_db, str(org_id), is_active=True)

        assert len(result) == 1


# ============ ItemService Tests ============


class TestCreateItem:
    """Tests for ItemService.create_item method."""

    def test_create_item_success(
        self, item_service, mock_db, org_id, sample_item_input
    ):
        """Test successful item creation."""
        # No existing item
        mock_db.query.return_value.filter.return_value.first.return_value = None

        # Category exists and is active
        category = MockItemCategory(
            category_id=sample_item_input.category_id,
            organization_id=org_id,
            is_active=True,
        )
        mock_db.get.return_value = category

        result = item_service.create_item(mock_db, org_id, sample_item_input)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once()

    def test_create_item_duplicate_code_fails(
        self, item_service, mock_db, org_id, sample_item_input
    ):
        """Test that duplicate item code fails."""
        from fastapi import HTTPException

        # Existing item found
        existing = MockItem(
            organization_id=org_id,
            item_code=sample_item_input.item_code,
        )
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        with pytest.raises(HTTPException) as exc:
            item_service.create_item(mock_db, org_id, sample_item_input)

        assert exc.value.status_code == 400
        assert "already exists" in exc.value.detail

    def test_create_item_invalid_category_fails(
        self, item_service, mock_db, org_id, sample_item_input
    ):
        """Test that invalid category fails."""
        from fastapi import HTTPException

        # No existing item
        mock_db.query.return_value.filter.return_value.first.return_value = None
        # Category not found
        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            item_service.create_item(mock_db, org_id, sample_item_input)

        assert exc.value.status_code == 404
        assert "category not found" in exc.value.detail.lower()

    def test_create_item_inactive_category_fails(
        self, item_service, mock_db, org_id, sample_item_input
    ):
        """Test that inactive category fails."""
        from fastapi import HTTPException

        # No existing item
        mock_db.query.return_value.filter.return_value.first.return_value = None
        # Category is inactive
        category = MockItemCategory(
            category_id=sample_item_input.category_id,
            organization_id=org_id,
            is_active=False,
        )
        mock_db.get.return_value = category

        with pytest.raises(HTTPException) as exc:
            item_service.create_item(mock_db, org_id, sample_item_input)

        assert exc.value.status_code == 400
        assert "not active" in exc.value.detail.lower()


class TestGetItem:
    """Tests for ItemService.get method."""

    def test_get_existing_item(self, item_service, mock_db, org_id):
        """Test getting existing item."""
        item = MockItem(organization_id=org_id)
        mock_db.get.return_value = item

        result = item_service.get(mock_db, str(item.item_id))

        assert result == item

    def test_get_nonexistent_item_fails(self, item_service, mock_db):
        """Test getting non-existent item raises 404."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            item_service.get(mock_db, str(uuid4()))

        assert exc.value.status_code == 404


class TestGetItemByCode:
    """Tests for ItemService.get_by_code method."""

    def test_get_by_code_success(self, item_service, mock_db, org_id):
        """Test getting item by code."""
        item = MockItem(organization_id=org_id, item_code="ITEM-001")
        mock_db.query.return_value.filter.return_value.first.return_value = item

        result = item_service.get_by_code(mock_db, org_id, "ITEM-001")

        assert result == item

    def test_get_by_code_not_found(self, item_service, mock_db, org_id):
        """Test getting non-existent item by code returns None."""
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = item_service.get_by_code(mock_db, org_id, "NOTFOUND")

        assert result is None


class TestUpdateItem:
    """Tests for ItemService.update_item method."""

    def test_update_item_success(self, item_service, mock_db, org_id):
        """Test successful item update."""
        item = MockItem(organization_id=org_id, item_name="Old Name")
        mock_db.get.return_value = item

        result = item_service.update_item(
            mock_db, org_id, item.item_id, {"item_name": "New Name"}
        )

        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once()
        assert result.item_name == "New Name"

    def test_update_nonexistent_item_fails(self, item_service, mock_db, org_id):
        """Test updating non-existent item fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            item_service.update_item(mock_db, org_id, uuid4(), {"item_name": "New"})

        assert exc.value.status_code == 404

    def test_update_wrong_org_fails(self, item_service, mock_db, org_id):
        """Test updating item from wrong organization fails."""
        from fastapi import HTTPException

        item = MockItem(organization_id=uuid4())  # Different org
        mock_db.get.return_value = item

        with pytest.raises(HTTPException) as exc:
            item_service.update_item(mock_db, org_id, item.item_id, {"item_name": "New"})

        assert exc.value.status_code == 404

    def test_update_immutable_field_fails(self, item_service, mock_db, org_id):
        """Test updating immutable fields fails."""
        from fastapi import HTTPException

        item = MockItem(organization_id=org_id)
        mock_db.get.return_value = item

        with pytest.raises(HTTPException) as exc:
            item_service.update_item(
                mock_db, org_id, item.item_id, {"item_code": "NEW-CODE"}
            )

        assert exc.value.status_code == 400
        assert "Cannot update" in exc.value.detail


class TestUpdateCost:
    """Tests for ItemService.update_cost method."""

    def test_update_average_cost(self, item_service, mock_db, org_id):
        """Test updating average cost."""
        item = MockItem(organization_id=org_id, average_cost=Decimal("10.00"))
        mock_db.get.return_value = item

        result = item_service.update_cost(
            mock_db, org_id, item.item_id, new_average_cost=Decimal("15.00")
        )

        assert result.average_cost == Decimal("15.00")
        mock_db.commit.assert_called_once()

    def test_update_multiple_costs(self, item_service, mock_db, org_id):
        """Test updating multiple cost fields."""
        item = MockItem(organization_id=org_id)
        mock_db.get.return_value = item

        result = item_service.update_cost(
            mock_db,
            org_id,
            item.item_id,
            new_average_cost=Decimal("15.00"),
            new_last_purchase_cost=Decimal("14.00"),
            new_standard_cost=Decimal("16.00"),
        )

        assert result.average_cost == Decimal("15.00")
        assert result.last_purchase_cost == Decimal("14.00")
        assert result.standard_cost == Decimal("16.00")

    def test_update_cost_nonexistent_item_fails(self, item_service, mock_db, org_id):
        """Test updating cost of non-existent item fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            item_service.update_cost(
                mock_db, org_id, uuid4(), new_average_cost=Decimal("15.00")
            )

        assert exc.value.status_code == 404


class TestDeactivateItem:
    """Tests for ItemService.deactivate_item method."""

    def test_deactivate_item_success(self, item_service, mock_db, org_id):
        """Test successful item deactivation."""
        item = MockItem(organization_id=org_id, is_active=True)
        mock_db.get.return_value = item

        result = item_service.deactivate_item(mock_db, org_id, item.item_id)

        assert result.is_active is False
        mock_db.commit.assert_called_once()

    def test_deactivate_nonexistent_item_fails(self, item_service, mock_db, org_id):
        """Test deactivating non-existent item fails."""
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            item_service.deactivate_item(mock_db, org_id, uuid4())

        assert exc.value.status_code == 404

    def test_deactivate_wrong_org_fails(self, item_service, mock_db, org_id):
        """Test deactivating item from wrong organization fails."""
        from fastapi import HTTPException

        item = MockItem(organization_id=uuid4())  # Different org
        mock_db.get.return_value = item

        with pytest.raises(HTTPException) as exc:
            item_service.deactivate_item(mock_db, org_id, item.item_id)

        assert exc.value.status_code == 404


class TestListItems:
    """Tests for ItemService.list method."""

    def test_list_all_items(self, item_service, mock_db, org_id):
        """Test listing all items."""
        items = [MockItem(organization_id=org_id) for _ in range(5)]
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = (
            items
        )

        result = item_service.list(mock_db, str(org_id))

        assert len(result) == 5

    def test_list_with_type_filter(self, item_service, mock_db, org_id):
        """Test listing items with type filter."""
        items = [MockItem(organization_id=org_id, item_type=MockItemType.INVENTORY)]
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = (
            items
        )

        result = item_service.list(mock_db, str(org_id), item_type=ItemType.INVENTORY)

        assert len(result) == 1

    def test_list_with_search(self, item_service, mock_db, org_id):
        """Test listing items with search filter."""
        items = [MockItem(organization_id=org_id, item_name="Test Widget")]
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = (
            items
        )

        result = item_service.list(mock_db, str(org_id), search="Widget")

        assert len(result) == 1
