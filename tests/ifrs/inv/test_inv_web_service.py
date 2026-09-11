"""
Tests for InventoryWebService.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

from unittest.mock import patch


class TestInvWebServiceHelpers:
    """Tests for inventory web service helper functions."""

    def test_format_date_with_value(self):
        """Test date formatting with valid date."""
        from app.services.inventory.web import _format_date

        result = _format_date(date(2024, 1, 15))
        assert result == "2024-01-15"

    def test_format_date_none(self):
        """Test date formatting with None."""
        from app.services.inventory.web import _format_date

        result = _format_date(None)
        assert result == ""

    def test_format_currency_usd(self):
        """Test currency formatting for USD."""
        from app.services.inventory.web import _format_currency

        result = _format_currency(Decimal("1234.56"), "USD")
        assert result == "USD 1,234.56"

    def test_format_currency_other(self):
        """Test currency formatting for other currencies."""
        from app.services.inventory.web import _format_currency

        result = _format_currency(Decimal("1234.56"), "EUR")
        assert result == "EUR 1,234.56"

    def test_format_currency_none(self):
        """Test currency formatting with None."""
        from app.services.inventory.web import _format_currency

        result = _format_currency(None)
        assert result == ""

    def test_parse_transaction_type_valid(self):
        """Test transaction type parsing with valid value."""
        from app.models.inventory.inventory_transaction import TransactionType
        from app.services.inventory.web import _parse_transaction_type

        result = _parse_transaction_type("RECEIPT")
        assert result == TransactionType.RECEIPT

    def test_parse_transaction_type_lowercase(self):
        """Test transaction type parsing with lowercase value."""
        from app.models.inventory.inventory_transaction import TransactionType
        from app.services.inventory.web import _parse_transaction_type

        result = _parse_transaction_type("receipt")
        assert result == TransactionType.RECEIPT

    def test_parse_transaction_type_none(self):
        """Test transaction type parsing with None."""
        from app.services.inventory.web import _parse_transaction_type

        result = _parse_transaction_type(None)
        assert result is None

    def test_parse_transaction_type_invalid(self):
        """Test transaction type parsing with invalid value."""
        from app.services.inventory.web import _parse_transaction_type

        result = _parse_transaction_type("INVALID_TYPE")
        assert result is None

    def test_try_uuid_valid(self):
        """Test UUID parsing with valid value."""
        from app.services.inventory.web import _try_uuid

        test_uuid = uuid.uuid4()
        result = _try_uuid(str(test_uuid))
        assert result == test_uuid

    def test_try_uuid_none(self):
        """Test UUID parsing with None."""
        from app.services.inventory.web import _try_uuid

        result = _try_uuid(None)
        assert result is None

    def test_try_uuid_invalid(self):
        """Test UUID parsing with invalid value."""
        from app.services.inventory.web import _try_uuid

        result = _try_uuid("not-a-uuid")
        assert result is None


class MockItem:
    """Mock Item for testing."""

    def __init__(self, **kwargs):
        from app.models.inventory.item import CostingMethod, ItemType

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
        self.track_inventory = kwargs.get("track_inventory", True)
        self.reorder_point = kwargs.get("reorder_point")


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
        from app.models.inventory.inventory_transaction import TransactionType

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
        from unittest.mock import patch

        from app.services.inventory.web import InventoryWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()

        mock_item = MockItem(organization_id=org_id)
        mock_category = MockItemCategory(organization_id=org_id)

        # SA2: db.scalar() called 3 times (total_count, active_count, stock_count)
        mock_db.scalar.side_effect = [1, 1, 1]
        # SA2: db.execute().all() returns rows (item, category)
        mock_db.execute.return_value.all.return_value = [(mock_item, mock_category)]
        # SA2: db.scalars().all() returns categories
        mock_db.scalars.return_value.all.return_value = []

        # Patch _get_batch_stock_quantities to avoid nested db.execute calls
        with patch(
            "app.services.inventory.web._get_batch_stock_quantities",
            return_value={},
        ):
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
        from app.services.inventory.web import InventoryWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()

        # SA2: db.scalar() called 3 times (total_count, active_count, stock_count)
        mock_db.scalar.side_effect = [0, 0, 0]
        mock_db.execute.return_value.all.return_value = []
        mock_db.scalars.return_value.all.return_value = []

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
        from app.services.inventory.web import InventoryWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()
        category_id = uuid.uuid4()

        mock_db.scalar.side_effect = [0, 0, 0]
        mock_db.execute.return_value.all.return_value = []
        mock_db.scalars.return_value.all.return_value = []

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
        from app.services.inventory.web import InventoryWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()

        mock_db.scalar.side_effect = [0, 0, 0]
        mock_db.execute.return_value.all.return_value = []
        mock_db.scalars.return_value.all.return_value = []

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
        from app.services.inventory.web import InventoryWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()

        mock_txn = MockInventoryTransaction(organization_id=org_id)
        mock_item = MockItem(organization_id=org_id)
        mock_warehouse = MockWarehouse(organization_id=org_id)

        # SA2: db.scalar() for count, db.execute().all() for rows
        mock_db.scalar.return_value = 1
        mock_db.execute.return_value.all.return_value = [
            (mock_txn, mock_item, mock_warehouse)
        ]

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
        from app.services.inventory.web import InventoryWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()

        mock_db.scalar.return_value = 0
        mock_db.execute.return_value.all.return_value = []

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
        from app.services.inventory.web import InventoryWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()

        mock_db.scalar.return_value = 0
        mock_db.execute.return_value.all.return_value = []

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
        from app.services.inventory.web import InventoryWebService

        mock_db = MagicMock()
        org_id = uuid.uuid4()

        mock_db.scalar.return_value = 100
        mock_db.execute.return_value.all.return_value = []

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


class TestInvTransactionWebService:
    """Tests for manual inventory transaction web adapters."""

    def test_create_transaction_response_passes_receipt_lot_number(self):
        """Receipt adapter should preserve the entered lot number."""
        from app.services.inventory.web import InventoryTransactionWebService

        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        item_id = uuid.uuid4()
        warehouse_id = uuid.uuid4()
        fiscal_period_id = uuid.uuid4()

        mock_auth = MagicMock()
        mock_auth.organization_id = org_id
        mock_auth.user_id = user_id

        mock_db = MagicMock()
        mock_fiscal_period = MagicMock()
        mock_fiscal_period.fiscal_period_id = fiscal_period_id
        mock_db.scalars.return_value.first.return_value = mock_fiscal_period

        with patch(
            "app.services.inventory.transaction.InventoryTransactionService.create_receipt"
        ) as mock_create_receipt:
            response = InventoryTransactionWebService.create_transaction_response(
                request=MagicMock(),
                auth=mock_auth,
                transaction_type="RECEIPT",
                item_id=str(item_id),
                warehouse_id=str(warehouse_id),
                quantity="5",
                unit_cost="10",
                transaction_date="2026-04-10",
                reference="REF-1",
                notes=None,
                lot_number="LOT-APR-001",
                db=mock_db,
                lot_service_start_date="2026-04-01",
                lot_service_end_date="2027-03-31",
                lot_provider_reference="CIRCUIT-12345",
                lot_document_reference="SLA-987",
            )

        txn_input = mock_create_receipt.call_args.args[2]
        assert txn_input.lot_number == "LOT-APR-001"
        assert txn_input.lot_manufacture_date == date(2026, 4, 1)
        assert txn_input.lot_expiry_date == date(2027, 3, 31)
        assert txn_input.lot_supplier_lot_number == "CIRCUIT-12345"
        assert txn_input.lot_certificate_of_analysis == "SLA-987"
        assert txn_input.lot_id is None
        assert response.status_code == 303
        mock_db.commit.assert_called_once()
        mock_db.rollback.assert_not_called()

    def test_create_transaction_response_auto_generates_receipt_serials(self):
        """Receipt adapter should generate serials when auto-generate is selected."""
        from app.services.inventory.web import InventoryTransactionWebService

        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        item_id = uuid.uuid4()
        warehouse_id = uuid.uuid4()
        fiscal_period_id = uuid.uuid4()

        mock_auth = MagicMock()
        mock_auth.organization_id = org_id
        mock_auth.user_id = user_id

        mock_item = MagicMock()
        mock_item.organization_id = org_id
        mock_item.item_code = "LAPTOP"

        mock_db = MagicMock()
        mock_db.get.return_value = mock_item
        mock_fiscal_period = MagicMock()
        mock_fiscal_period.fiscal_period_id = fiscal_period_id
        mock_db.scalars.return_value.first.return_value = mock_fiscal_period

        with (
            patch(
                "app.services.inventory.web._generate_receipt_serial_numbers",
                return_value=["LAPTOP-20260410-0001", "LAPTOP-20260410-0002"],
            ) as mock_generate,
            patch(
                "app.services.inventory.transaction.InventoryTransactionService.create_receipt"
            ) as mock_create_receipt,
        ):
            response = InventoryTransactionWebService.create_transaction_response(
                request=MagicMock(),
                auth=mock_auth,
                transaction_type="RECEIPT",
                item_id=str(item_id),
                warehouse_id=str(warehouse_id),
                quantity="2",
                unit_cost="10",
                transaction_date="2026-04-10",
                reference="REF-1",
                notes=None,
                lot_number=None,
                db=mock_db,
                serial_auto_generate=True,
                serial_prefix="LAPTOP",
            )

        mock_generate.assert_called_once()
        txn_input = mock_create_receipt.call_args.args[2]
        assert txn_input.serial_numbers == [
            "LAPTOP-20260410-0001",
            "LAPTOP-20260410-0002",
        ]
        assert response.status_code == 303
        mock_db.commit.assert_called_once()
        mock_db.rollback.assert_not_called()

    def test_create_transaction_response_rejects_manual_and_auto_serials(self):
        """Receipt adapter should not accept manual serials and auto-generation together."""
        from app.services.inventory.web import InventoryTransactionWebService

        mock_auth = MagicMock()
        mock_auth.organization_id = uuid.uuid4()
        mock_auth.user_id = uuid.uuid4()
        mock_db = MagicMock()

        with patch(
            "app.services.inventory.transaction.InventoryTransactionService.create_receipt"
        ) as mock_create_receipt:
            response = InventoryTransactionWebService.create_transaction_response(
                request=MagicMock(),
                auth=mock_auth,
                transaction_type="RECEIPT",
                item_id=str(uuid.uuid4()),
                warehouse_id=str(uuid.uuid4()),
                quantity="2",
                unit_cost="10",
                transaction_date="2026-04-10",
                reference="REF-1",
                notes=None,
                lot_number=None,
                db=mock_db,
                serial_numbers="SN-001\nSN-002",
                serial_auto_generate=True,
            )

        mock_create_receipt.assert_not_called()
        assert response.status_code == 303
        assert response.headers["location"] == (
            "/inventory/transactions?error=transaction_failed"
        )
        assert "serial" not in response.headers["location"]
        mock_db.commit.assert_not_called()
        mock_db.rollback.assert_called_once()

    def test_create_transaction_response_resolves_issue_lot_number_to_lot_id(self):
        """Issue adapter should resolve a lot number to a warehouse-scoped lot id."""
        from app.services.inventory.web import InventoryTransactionWebService

        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        item_id = uuid.uuid4()
        warehouse_id = uuid.uuid4()
        fiscal_period_id = uuid.uuid4()
        lot_id = uuid.uuid4()

        mock_auth = MagicMock()
        mock_auth.organization_id = org_id
        mock_auth.user_id = user_id

        mock_db = MagicMock()
        mock_fiscal_period = MagicMock()
        mock_fiscal_period.fiscal_period_id = fiscal_period_id
        mock_db.scalars.return_value.first.return_value = mock_fiscal_period

        with (
            patch(
                "app.services.inventory.web._resolve_lot_id_for_item_warehouse",
                return_value=lot_id,
            ) as mock_resolve,
            patch(
                "app.services.inventory.transaction.InventoryTransactionService.create_issue"
            ) as mock_create_issue,
        ):
            response = InventoryTransactionWebService.create_transaction_response(
                request=MagicMock(),
                auth=mock_auth,
                transaction_type="ISSUE",
                item_id=str(item_id),
                warehouse_id=str(warehouse_id),
                quantity="2",
                unit_cost="10",
                transaction_date="2026-04-10",
                reference="REF-2",
                notes=None,
                lot_number="LOT-APR-002",
                db=mock_db,
            )

        txn_input = mock_create_issue.call_args.args[2]
        mock_resolve.assert_called_once_with(
            mock_db,
            organization_id=org_id,
            item_id=item_id,
            warehouse_id=warehouse_id,
            lot_number="LOT-APR-002",
        )
        assert txn_input.lot_id == lot_id
        assert txn_input.lot_number == "LOT-APR-002"
        assert response.status_code == 303
        mock_db.commit.assert_called_once()
        mock_db.rollback.assert_not_called()

    def test_create_transfer_response_resolves_lot_number_from_source_warehouse(self):
        """Transfer adapter should resolve the entered lot against the source warehouse."""
        from app.services.inventory.web import InventoryTransactionWebService

        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        item_id = uuid.uuid4()
        from_warehouse_id = uuid.uuid4()
        to_warehouse_id = uuid.uuid4()
        fiscal_period_id = uuid.uuid4()
        lot_id = uuid.uuid4()

        mock_auth = MagicMock()
        mock_auth.organization_id = org_id
        mock_auth.user_id = user_id

        mock_db = MagicMock()
        mock_fiscal_period = MagicMock()
        mock_fiscal_period.fiscal_period_id = fiscal_period_id
        mock_item = MagicMock()
        mock_item.organization_id = org_id
        mock_item.average_cost = Decimal("12.50")
        mock_item.base_uom = "EA"
        mock_item.currency_code = "USD"
        mock_db.scalars.return_value.first.return_value = mock_fiscal_period
        mock_db.get.return_value = mock_item

        with (
            patch(
                "app.services.inventory.web._resolve_lot_id_for_item_warehouse",
                return_value=lot_id,
            ) as mock_resolve,
            patch(
                "app.services.inventory.transaction.InventoryTransactionService.create_transfer"
            ) as mock_create_transfer,
        ):
            response = InventoryTransactionWebService.create_transfer_response(
                request=MagicMock(),
                auth=mock_auth,
                item_id=str(item_id),
                from_warehouse_id=str(from_warehouse_id),
                to_warehouse_id=str(to_warehouse_id),
                quantity="8",
                transaction_date="2026-04-10",
                reference="REF-3",
                notes=None,
                lot_number="LOT-APR-003",
                db=mock_db,
            )

        txn_input = mock_create_transfer.call_args.kwargs["input"]
        mock_resolve.assert_called_once_with(
            mock_db,
            organization_id=org_id,
            item_id=item_id,
            warehouse_id=from_warehouse_id,
            lot_number="LOT-APR-003",
        )
        assert txn_input.lot_id == lot_id
        assert txn_input.lot_number == "LOT-APR-003"
        assert response.status_code == 303
        mock_db.commit.assert_called_once()
        mock_db.rollback.assert_not_called()

    def test_create_transaction_response_rolls_back_on_error(self):
        """Manual transaction adapter should roll back failed writes."""
        from app.services.inventory.web import InventoryTransactionWebService

        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        item_id = uuid.uuid4()
        warehouse_id = uuid.uuid4()
        fiscal_period_id = uuid.uuid4()

        mock_auth = MagicMock()
        mock_auth.organization_id = org_id
        mock_auth.user_id = user_id

        mock_db = MagicMock()
        mock_fiscal_period = MagicMock()
        mock_fiscal_period.fiscal_period_id = fiscal_period_id
        mock_db.scalars.return_value.first.return_value = mock_fiscal_period

        with patch(
            "app.services.inventory.transaction.InventoryTransactionService.create_receipt",
            side_effect=RuntimeError("receipt failed"),
        ):
            response = InventoryTransactionWebService.create_transaction_response(
                request=MagicMock(),
                auth=mock_auth,
                transaction_type="RECEIPT",
                item_id=str(item_id),
                warehouse_id=str(warehouse_id),
                quantity="5",
                unit_cost="10",
                transaction_date="2026-04-10",
                reference="REF-1",
                notes=None,
                lot_number=None,
                db=mock_db,
            )

        mock_db.commit.assert_not_called()
        mock_db.rollback.assert_called_once()
        assert response.status_code == 303
        assert response.headers["location"] == (
            "/inventory/transactions?error=transaction_failed"
        )
        assert "receipt%20failed" not in response.headers["location"]

    def test_create_adjustment_response_commits_successful_write(self):
        """Adjustment adapter should commit successful manual stock adjustments."""
        from app.services.inventory.web import InventoryTransactionWebService

        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        item_id = uuid.uuid4()
        warehouse_id = uuid.uuid4()
        fiscal_period_id = uuid.uuid4()

        mock_auth = MagicMock()
        mock_auth.organization_id = org_id
        mock_auth.user_id = user_id

        mock_db = MagicMock()
        mock_fiscal_period = MagicMock()
        mock_fiscal_period.fiscal_period_id = fiscal_period_id
        mock_db.scalars.return_value.first.return_value = mock_fiscal_period

        with patch(
            "app.services.inventory.transaction.InventoryTransactionService.create_adjustment"
        ) as mock_create_adjustment:
            response = InventoryTransactionWebService.create_adjustment_response(
                request=MagicMock(),
                auth=mock_auth,
                item_id=str(item_id),
                warehouse_id=str(warehouse_id),
                quantity="3",
                unit_cost="12.50",
                transaction_date="2026-04-10",
                adjustment_type="INCREASE",
                reason="COUNT",
                reference="REF-4",
                db=mock_db,
            )

        mock_create_adjustment.assert_called_once()
        mock_db.commit.assert_called_once()
        mock_db.rollback.assert_not_called()
        assert response.status_code == 303
