"""
Tests for InventoryWebService.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest


class TestInvWebServiceHelpers:
    """Tests for inventory web service helper functions."""

    def test_format_date_with_value(self):
        """Test date formatting with valid date."""
        from app.services.finance.inv.web import _format_date

        result = _format_date(date(2024, 1, 15))
        assert result == "2024-01-15"

    def test_format_date_none(self):
        """Test date formatting with None."""
        from app.services.finance.inv.web import _format_date

        result = _format_date(None)
        assert result == ""

    def test_format_currency_usd(self):
        """Test currency formatting for USD."""
        from app.services.finance.inv.web import _format_currency

        result = _format_currency(Decimal("1234.56"), "USD")
        assert result == "$1,234.56"

    def test_format_currency_other(self):
        """Test currency formatting for other currencies."""
        from app.services.finance.inv.web import _format_currency

        result = _format_currency(Decimal("1234.56"), "EUR")
        assert result == "EUR 1,234.56"

    def test_format_currency_none(self):
        """Test currency formatting with None."""
        from app.services.finance.inv.web import _format_currency

        result = _format_currency(None)
        assert result == ""

    def test_parse_transaction_type_valid(self):
        """Test transaction type parsing with valid value."""
        from app.services.finance.inv.web import _parse_transaction_type
        from app.models.finance.inv.inventory_transaction import TransactionType

        result = _parse_transaction_type("RECEIPT")
        assert result == TransactionType.RECEIPT

    def test_parse_transaction_type_lowercase(self):
        """Test transaction type parsing with lowercase value."""
        from app.services.finance.inv.web import _parse_transaction_type
        from app.models.finance.inv.inventory_transaction import TransactionType

        result = _parse_transaction_type("receipt")
        assert result == TransactionType.RECEIPT

    def test_parse_transaction_type_none(self):
        """Test transaction type parsing with None."""
        from app.services.finance.inv.web import _parse_transaction_type

        result = _parse_transaction_type(None)
        assert result is None

    def test_parse_transaction_type_invalid(self):
        """Test transaction type parsing with invalid value."""
        from app.services.finance.inv.web import _parse_transaction_type

        result = _parse_transaction_type("INVALID_TYPE")
        assert result is None

    def test_try_uuid_valid(self):
        """Test UUID parsing with valid value."""
        from app.services.finance.inv.web import _try_uuid

        test_uuid = uuid.uuid4()
        result = _try_uuid(str(test_uuid))
        assert result == test_uuid

    def test_try_uuid_none(self):
        """Test UUID parsing with None."""
        from app.services.finance.inv.web import _try_uuid

        result = _try_uuid(None)
        assert result is None

    def test_try_uuid_invalid(self):
        """Test UUID parsing with invalid value."""
        from app.services.finance.inv.web import _try_uuid

        result = _try_uuid("not-a-uuid")
        assert result is None


class MockItem:
    """Mock Item for testing."""

    def __init__(self, **kwargs):
        from app.models.finance.inv.item import ItemType, CostingMethod

        self.item_id = kwargs.get("item_id", uuid.uuid4())
        self.organization_id = kwargs.get("organization_id", uuid.uuid4())
        self.item_code = kwargs.get("item_code", "ITEM-001")
        self.item_name = kwargs.get("item_name", "Test Item")
        self.category_id = kwargs.get("category_id", uuid.uuid4())
        self.item_type = kwargs.get("item_type", ItemType.INVENTORY)
        self.costing_method = kwargs.get("costing_method", CostingMethod.FIFO)
        self.standard_cost = kwargs.get("standard_cost", Decimal("100.00"))
        self.list_price = kwargs.get("list_price", Decimal("150.00"))
        self.currency_code = kwargs.get("currency_code", "USD")
        self.is_active = kwargs.get("is_active", True)
        self.barcode = kwargs.get("barcode")


class MockItemCategory:
    """Mock ItemCategory for testing."""

    def __init__(self, **kwargs):
        self.category_id = kwargs.get("category_id", uuid.uuid4())
        self.organization_id = kwargs.get("organization_id", uuid.uuid4())
        self.category_code = kwargs.get("category_code", "RAW")
        self.category_name = kwargs.get("category_name", "Raw Materials")
        self.is_active = kwargs.get("is_active", True)


class MockWarehouse:
    """Mock Warehouse for testing."""

    def __init__(self, **kwargs):
        self.warehouse_id = kwargs.get("warehouse_id", uuid.uuid4())
        self.organization_id = kwargs.get("organization_id", uuid.uuid4())
        self.warehouse_code = kwargs.get("warehouse_code", "WH-001")
        self.warehouse_name = kwargs.get("warehouse_name", "Main Warehouse")


class MockInventoryTransaction:
    """Mock InventoryTransaction for testing."""

    def __init__(self, **kwargs):
        from app.models.finance.inv.inventory_transaction import TransactionType

        self.transaction_id = kwargs.get("transaction_id", uuid.uuid4())
        self.organization_id = kwargs.get("organization_id", uuid.uuid4())
        self.item_id = kwargs.get("item_id", uuid.uuid4())
        self.warehouse_id = kwargs.get("warehouse_id", uuid.uuid4())
        self.transaction_date = kwargs.get("transaction_date", date.today())
        self.transaction_type = kwargs.get("transaction_type", TransactionType.RECEIPT)
        self.quantity = kwargs.get("quantity", Decimal("10.00"))
        self.uom = kwargs.get("uom", "EA")
        self.unit_cost = kwargs.get("unit_cost", Decimal("100.00"))
        self.total_cost = kwargs.get("total_cost", Decimal("1000.00"))
        self.currency_code = kwargs.get("currency_code", "USD")
        self.reference = kwargs.get("reference", "REF-001")


class TestInvWebServiceListItems:
    """Tests for list_items_context method."""

    def test_list_items_context_success(self):
        """Test successful items list context."""
        from app.services.finance.inv.web import InventoryWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()

        mock_item = MockItem(organization_id=org_id)
        mock_category = MockItemCategory(organization_id=org_id)

        mock_query = MagicMock()
        mock_query.join.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.with_entities.return_value = mock_query
        mock_query.scalar.return_value = 1
        mock_query.all.return_value = [(mock_item, mock_category)]

        mock_db.query.return_value = mock_query

        result = InventoryWebService.list_items_context(
            mock_db,
            str(org_id),
            search=None,
            category=None,
            page=1,
        )

        assert "items" in result
        assert len(result["items"]) == 1
        assert result["page"] == 1
        assert result["total_count"] == 1

    def test_list_items_context_with_search(self):
        """Test items list context with search filter."""
        from app.services.finance.inv.web import InventoryWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()

        mock_query = MagicMock()
        mock_query.join.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.with_entities.return_value = mock_query
        mock_query.scalar.return_value = 0
        mock_query.all.return_value = []

        mock_db.query.return_value = mock_query

        result = InventoryWebService.list_items_context(
            mock_db,
            str(org_id),
            search="widget",
            category=None,
            page=1,
        )

        assert result["search"] == "widget"

    def test_list_items_context_with_category_uuid(self):
        """Test items list context with category UUID filter."""
        from app.services.finance.inv.web import InventoryWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()
        category_id = uuid.uuid4()

        mock_query = MagicMock()
        mock_query.join.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.with_entities.return_value = mock_query
        mock_query.scalar.return_value = 0
        mock_query.all.return_value = []

        mock_db.query.return_value = mock_query

        result = InventoryWebService.list_items_context(
            mock_db,
            str(org_id),
            search=None,
            category=str(category_id),
            page=1,
        )

        assert result["category"] == str(category_id)

    def test_list_items_context_with_category_code(self):
        """Test items list context with category code filter."""
        from app.services.finance.inv.web import InventoryWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()

        mock_query = MagicMock()
        mock_query.join.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.with_entities.return_value = mock_query
        mock_query.scalar.return_value = 0
        mock_query.all.return_value = []

        mock_db.query.return_value = mock_query

        result = InventoryWebService.list_items_context(
            mock_db,
            str(org_id),
            search=None,
            category="RAW",
            page=1,
        )

        assert result["category"] == "RAW"


class TestInvWebServiceListTransactions:
    """Tests for list_transactions_context method."""

    def test_list_transactions_context_success(self):
        """Test successful transactions list context."""
        from app.services.finance.inv.web import InventoryWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()

        mock_txn = MockInventoryTransaction(organization_id=org_id)
        mock_item = MockItem(organization_id=org_id)
        mock_warehouse = MockWarehouse(organization_id=org_id)

        mock_query = MagicMock()
        mock_query.join.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.with_entities.return_value = mock_query
        mock_query.scalar.return_value = 1
        mock_query.all.return_value = [(mock_txn, mock_item, mock_warehouse)]

        mock_db.query.return_value = mock_query

        result = InventoryWebService.list_transactions_context(
            mock_db,
            str(org_id),
            search=None,
            transaction_type=None,
            page=1,
        )

        assert "transactions" in result
        assert len(result["transactions"]) == 1
        assert result["total_count"] == 1

    def test_list_transactions_context_with_type_filter(self):
        """Test transactions list context with type filter."""
        from app.services.finance.inv.web import InventoryWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()

        mock_query = MagicMock()
        mock_query.join.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.with_entities.return_value = mock_query
        mock_query.scalar.return_value = 0
        mock_query.all.return_value = []

        mock_db.query.return_value = mock_query

        result = InventoryWebService.list_transactions_context(
            mock_db,
            str(org_id),
            search=None,
            transaction_type="RECEIPT",
            page=1,
        )

        assert result["transaction_type"] == "RECEIPT"

    def test_list_transactions_context_with_search(self):
        """Test transactions list context with search filter."""
        from app.services.finance.inv.web import InventoryWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()

        mock_query = MagicMock()
        mock_query.join.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.with_entities.return_value = mock_query
        mock_query.scalar.return_value = 0
        mock_query.all.return_value = []

        mock_db.query.return_value = mock_query

        result = InventoryWebService.list_transactions_context(
            mock_db,
            str(org_id),
            search="REF-001",
            transaction_type=None,
            page=1,
        )

        assert result["search"] == "REF-001"

    def test_list_transactions_context_pagination(self):
        """Test transactions list context pagination."""
        from app.services.finance.inv.web import InventoryWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()

        mock_query = MagicMock()
        mock_query.join.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.offset.return_value = mock_query
        mock_query.with_entities.return_value = mock_query
        mock_query.scalar.return_value = 100
        mock_query.all.return_value = []

        mock_db.query.return_value = mock_query

        result = InventoryWebService.list_transactions_context(
            mock_db,
            str(org_id),
            search=None,
            transaction_type=None,
            page=3,
            limit=25,
        )

        assert result["page"] == 3
        assert result["limit"] == 25
        assert result["offset"] == 50
        assert result["total_pages"] == 4
