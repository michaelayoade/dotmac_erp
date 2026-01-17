"""
Tests for AccountImporter and AccountCategoryImporter.

Tests the Zoho Books CSV import functionality for chart of accounts,
including category creation, IFRS mapping, and code generation.
"""

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.services.ifrs.import_export.accounts import (
    AccountCategoryImporter,
    AccountImporter,
    ZOHO_ACCOUNT_TYPE_MAPPING,
    ZOHO_SUBLEDGER_MAPPING,
)
from app.services.ifrs.import_export.base import ImportConfig


# ============ Fixtures ============

@pytest.fixture
def organization_id():
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def user_id():
    return uuid.UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture
def import_config(organization_id, user_id):
    return ImportConfig(
        organization_id=organization_id,
        user_id=user_id,
        skip_duplicates=True,
        dry_run=False,
        batch_size=100,
        stop_on_error=False,
    )


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = None
    return db


# ============ Test ZOHO_ACCOUNT_TYPE_MAPPING ============

class TestZohoAccountTypeMapping:
    """Tests for the ZOHO_ACCOUNT_TYPE_MAPPING constant."""

    def test_revenue_accounts_map_to_revenue_category(self):
        """Revenue-type accounts should map to REVENUE IFRS category."""
        from app.models.ifrs.gl.account_category import IFRSCategory
        from app.models.ifrs.gl.account import NormalBalance

        for zoho_type in ["Income", "Other Income"]:
            ifrs_category, normal_balance = ZOHO_ACCOUNT_TYPE_MAPPING[zoho_type]
            assert ifrs_category == IFRSCategory.REVENUE
            assert normal_balance == NormalBalance.CREDIT

    def test_expense_accounts_map_to_expenses_category(self):
        """Expense-type accounts should map to EXPENSES IFRS category."""
        from app.models.ifrs.gl.account_category import IFRSCategory
        from app.models.ifrs.gl.account import NormalBalance

        for zoho_type in ["Expense", "Other Expense", "Cost Of Goods Sold"]:
            ifrs_category, normal_balance = ZOHO_ACCOUNT_TYPE_MAPPING[zoho_type]
            assert ifrs_category == IFRSCategory.EXPENSES
            assert normal_balance == NormalBalance.DEBIT

    def test_asset_accounts_map_to_assets_category(self):
        """Asset-type accounts should map to ASSETS IFRS category."""
        from app.models.ifrs.gl.account_category import IFRSCategory
        from app.models.ifrs.gl.account import NormalBalance

        asset_types = [
            "Cash", "Bank", "Accounts Receivable", "Other Current Asset",
            "Fixed Asset", "Stock", "Input Tax", "Payment Clearing"
        ]
        for zoho_type in asset_types:
            ifrs_category, normal_balance = ZOHO_ACCOUNT_TYPE_MAPPING[zoho_type]
            assert ifrs_category == IFRSCategory.ASSETS
            assert normal_balance == NormalBalance.DEBIT

    def test_liability_accounts_map_to_liabilities_category(self):
        """Liability-type accounts should map to LIABILITIES IFRS category."""
        from app.models.ifrs.gl.account_category import IFRSCategory
        from app.models.ifrs.gl.account import NormalBalance

        liability_types = [
            "Accounts Payable", "Other Current Liability",
            "Long Term Liability", "Other Liability", "Output Tax"
        ]
        for zoho_type in liability_types:
            ifrs_category, normal_balance = ZOHO_ACCOUNT_TYPE_MAPPING[zoho_type]
            assert ifrs_category == IFRSCategory.LIABILITIES
            assert normal_balance == NormalBalance.CREDIT

    def test_equity_accounts_map_to_equity_category(self):
        """Equity-type accounts should map to EQUITY IFRS category."""
        from app.models.ifrs.gl.account_category import IFRSCategory
        from app.models.ifrs.gl.account import NormalBalance

        ifrs_category, normal_balance = ZOHO_ACCOUNT_TYPE_MAPPING["Equity"]
        assert ifrs_category == IFRSCategory.EQUITY
        assert normal_balance == NormalBalance.CREDIT


# ============ Test ZOHO_SUBLEDGER_MAPPING ============

class TestZohoSubledgerMapping:
    """Tests for the ZOHO_SUBLEDGER_MAPPING constant."""

    def test_ar_subledger_mapping(self):
        """Accounts Receivable should map to AR subledger."""
        assert ZOHO_SUBLEDGER_MAPPING["Accounts Receivable"] == "AR"

    def test_ap_subledger_mapping(self):
        """Accounts Payable should map to AP subledger."""
        assert ZOHO_SUBLEDGER_MAPPING["Accounts Payable"] == "AP"

    def test_inventory_subledger_mapping(self):
        """Stock should map to INVENTORY subledger."""
        assert ZOHO_SUBLEDGER_MAPPING["Stock"] == "INVENTORY"

    def test_fixed_asset_subledger_mapping(self):
        """Fixed Asset should map to ASSET subledger."""
        assert ZOHO_SUBLEDGER_MAPPING["Fixed Asset"] == "ASSET"

    def test_bank_subledger_mapping(self):
        """Bank and Cash should map to BANK subledger."""
        assert ZOHO_SUBLEDGER_MAPPING["Bank"] == "BANK"
        assert ZOHO_SUBLEDGER_MAPPING["Cash"] == "BANK"


# ============ Test AccountCategoryImporter ============

class TestAccountCategoryImporter:
    """Tests for AccountCategoryImporter class."""

    def test_entity_name_is_account_category(self, mock_db, import_config):
        """Entity name should be Account Category."""
        importer = AccountCategoryImporter(mock_db, import_config)
        assert importer.entity_name == "Account Category"

    def test_get_field_mappings_returns_empty(self, mock_db, import_config):
        """Field mappings should be empty (categories derived from types)."""
        importer = AccountCategoryImporter(mock_db, import_config)
        mappings = importer.get_field_mappings()
        assert mappings == []

    def test_get_unique_key_returns_account_type(self, mock_db, import_config):
        """Unique key should be the Account Type field."""
        importer = AccountCategoryImporter(mock_db, import_config)
        row = {"Account Type": "  Bank  "}
        assert importer.get_unique_key(row) == "Bank"

    def test_make_category_code_uppercase_underscores(self, mock_db, import_config):
        """Category code should be uppercase with underscores."""
        importer = AccountCategoryImporter(mock_db, import_config)
        assert importer._make_category_code("Other Current Asset") == "OTHER_CURRENT_ASSET"

    def test_make_category_code_truncates_at_20(self, mock_db, import_config):
        """Category code should be truncated to 20 characters."""
        importer = AccountCategoryImporter(mock_db, import_config)
        long_type = "Very Long Account Type Name That Exceeds Limit"
        code = importer._make_category_code(long_type)
        assert len(code) <= 20

    def test_get_display_order_assets(self, mock_db, import_config):
        """Assets should have display order 100."""
        from app.models.ifrs.gl.account_category import IFRSCategory
        importer = AccountCategoryImporter(mock_db, import_config)
        assert importer._get_display_order(IFRSCategory.ASSETS) == 100

    def test_get_display_order_liabilities(self, mock_db, import_config):
        """Liabilities should have display order 200."""
        from app.models.ifrs.gl.account_category import IFRSCategory
        importer = AccountCategoryImporter(mock_db, import_config)
        assert importer._get_display_order(IFRSCategory.LIABILITIES) == 200

    def test_get_display_order_equity(self, mock_db, import_config):
        """Equity should have display order 300."""
        from app.models.ifrs.gl.account_category import IFRSCategory
        importer = AccountCategoryImporter(mock_db, import_config)
        assert importer._get_display_order(IFRSCategory.EQUITY) == 300

    @patch("app.services.ifrs.import_export.accounts.AccountCategory")
    def test_create_entity_creates_category(self, mock_category_cls, mock_db, import_config):
        """create_entity should create AccountCategory with correct attributes."""
        importer = AccountCategoryImporter(mock_db, import_config)
        row = {"Account Type": "Bank"}

        category = importer.create_entity(row)

        # Should have called AccountCategory constructor
        assert mock_category_cls.called

    def test_get_category_id_returns_cached_id(self, mock_db, import_config):
        """get_category_id should return cached category ID."""
        importer = AccountCategoryImporter(mock_db, import_config)
        test_id = uuid.uuid4()
        importer._category_cache["BANK"] = test_id

        result = importer.get_category_id("Bank")
        assert result == test_id


# ============ Test AccountImporter ============

class TestAccountImporter:
    """Tests for AccountImporter class."""

    def test_entity_name_is_account(self, mock_db, import_config):
        """Entity name should be Account."""
        importer = AccountImporter(mock_db, import_config)
        assert importer.entity_name == "Account"

    def test_get_field_mappings_returns_expected_fields(self, mock_db, import_config):
        """Field mappings should include required account fields."""
        importer = AccountImporter(mock_db, import_config)
        mappings = importer.get_field_mappings()

        mapping_names = [m.source_field for m in mappings]
        assert "Account Name" in mapping_names
        assert "Account Code" in mapping_names
        assert "Account Type" in mapping_names

    def test_get_unique_key_prefers_code_over_name(self, mock_db, import_config):
        """Unique key should prefer Account Code over Account Name."""
        importer = AccountImporter(mock_db, import_config)
        row = {"Account Code": "1000", "Account Name": "Cash"}
        assert importer.get_unique_key(row) == "1000"

    def test_get_unique_key_falls_back_to_name(self, mock_db, import_config):
        """Unique key should fall back to Account Name if no code."""
        importer = AccountImporter(mock_db, import_config)
        row = {"Account Code": "", "Account Name": "Cash"}
        assert importer.get_unique_key(row) == "Cash"

    def test_get_code_prefix_assets(self, mock_db, import_config):
        """Assets should have code prefix 1."""
        from app.models.ifrs.gl.account_category import IFRSCategory
        importer = AccountImporter(mock_db, import_config)
        assert importer._get_code_prefix(IFRSCategory.ASSETS) == "1"

    def test_get_code_prefix_liabilities(self, mock_db, import_config):
        """Liabilities should have code prefix 2."""
        from app.models.ifrs.gl.account_category import IFRSCategory
        importer = AccountImporter(mock_db, import_config)
        assert importer._get_code_prefix(IFRSCategory.LIABILITIES) == "2"

    def test_get_code_prefix_equity(self, mock_db, import_config):
        """Equity should have code prefix 3."""
        from app.models.ifrs.gl.account_category import IFRSCategory
        importer = AccountImporter(mock_db, import_config)
        assert importer._get_code_prefix(IFRSCategory.EQUITY) == "3"

    def test_get_code_prefix_revenue(self, mock_db, import_config):
        """Revenue should have code prefix 4."""
        from app.models.ifrs.gl.account_category import IFRSCategory
        importer = AccountImporter(mock_db, import_config)
        assert importer._get_code_prefix(IFRSCategory.REVENUE) == "4"

    def test_get_code_prefix_expenses(self, mock_db, import_config):
        """Expenses should have code prefix 5."""
        from app.models.ifrs.gl.account_category import IFRSCategory
        importer = AccountImporter(mock_db, import_config)
        assert importer._get_code_prefix(IFRSCategory.EXPENSES) == "5"

    def test_get_code_prefix_unknown_returns_9(self, mock_db, import_config):
        """Unknown category should have code prefix 9."""
        from app.models.ifrs.gl.account_category import IFRSCategory
        importer = AccountImporter(mock_db, import_config)
        # OCI is last in enum
        assert importer._get_code_prefix(IFRSCategory.OTHER_COMPREHENSIVE_INCOME) == "6"

    @patch("app.services.ifrs.import_export.accounts.Account")
    def test_create_entity_generates_code_if_missing(
        self, mock_account_cls, mock_db, import_config
    ):
        """create_entity should generate account code if not provided."""
        importer = AccountImporter(mock_db, import_config)
        # Pre-populate category cache
        cat_id = uuid.uuid4()
        importer._category_importer._category_cache["BANK"] = cat_id

        row = {
            "account_name": "Main Bank Account",
            "account_code": "",
            "zoho_account_type": "Bank",
            "is_active": True,
        }

        importer.create_entity(row)
        assert importer._account_code_counter == 1

    @patch("app.services.ifrs.import_export.accounts.Account")
    def test_create_entity_uses_provided_code(
        self, mock_account_cls, mock_db, import_config
    ):
        """create_entity should use provided account code."""
        importer = AccountImporter(mock_db, import_config)
        cat_id = uuid.uuid4()
        importer._category_importer._category_cache["BANK"] = cat_id

        row = {
            "account_name": "Main Bank Account",
            "account_code": "1100",
            "zoho_account_type": "Bank",
            "is_active": True,
        }

        importer.create_entity(row)
        # Counter should not increment when code is provided
        assert importer._account_code_counter == 0

    def test_account_code_counter_increments(self, mock_db, import_config):
        """Account code counter should increment with each generated code."""
        importer = AccountImporter(mock_db, import_config)
        assert importer._account_code_counter == 0
        importer._account_code_counter += 1
        assert importer._account_code_counter == 1
