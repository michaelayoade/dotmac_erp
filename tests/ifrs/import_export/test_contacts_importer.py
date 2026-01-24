"""
Tests for CustomerImporter and SupplierImporter.

Tests the Zoho Books CSV import functionality for customers and suppliers,
including type determination, address building, and code generation.
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.services.finance.import_export.contacts import (
    CustomerImporter,
    SupplierImporter,
    get_ar_control_account,
    get_ap_control_account,
)
from app.services.finance.import_export.base import ImportConfig


# ============ Fixtures ============

@pytest.fixture
def organization_id():
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def user_id():
    return uuid.UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture
def ar_control_account_id():
    return uuid.UUID("00000000-0000-0000-0000-000000000003")


@pytest.fixture
def ap_control_account_id():
    return uuid.UUID("00000000-0000-0000-0000-000000000004")


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


# ============ Test CustomerImporter ============

class TestCustomerImporter:
    """Tests for CustomerImporter class."""

    def test_entity_name_is_customer(self, mock_db, import_config, ar_control_account_id):
        """Entity name should be Customer."""
        importer = CustomerImporter(mock_db, import_config, ar_control_account_id)
        assert importer.entity_name == "Customer"

    def test_get_field_mappings_includes_required_fields(
        self, mock_db, import_config, ar_control_account_id
    ):
        """Field mappings should include Display Name as required."""
        importer = CustomerImporter(mock_db, import_config, ar_control_account_id)
        mappings = importer.get_field_mappings()

        mapping_names = [m.source_field for m in mappings]
        assert "Display Name" in mapping_names

        # Find the Display Name mapping and verify it's required
        display_name_mapping = next(m for m in mappings if m.source_field == "Display Name")
        assert display_name_mapping.required is True

    def test_get_unique_key_returns_display_name(
        self, mock_db, import_config, ar_control_account_id
    ):
        """Unique key should be the Display Name field."""
        importer = CustomerImporter(mock_db, import_config, ar_control_account_id)
        row = {"Display Name": "  Acme Corp  "}
        assert importer.get_unique_key(row) == "Acme Corp"

    @patch("app.services.ifrs.import_export.contacts.Customer")
    def test_create_entity_company_type_with_company_name(
        self, mock_customer_cls, mock_db, import_config, ar_control_account_id
    ):
        """Customer type should be COMPANY when company_name is provided."""
        from app.models.finance.ar.customer import CustomerType

        importer = CustomerImporter(mock_db, import_config, ar_control_account_id)
        row = {
            "display_name": "Acme Corp",
            "company_name": "Acme Corporation Ltd",
            "first_name": "",
            "last_name": "",
        }

        importer.create_entity(row)

        # Verify CustomerType.COMPANY was used
        call_kwargs = mock_customer_cls.call_args[1]
        assert call_kwargs["customer_type"] == CustomerType.COMPANY

    @patch("app.services.ifrs.import_export.contacts.Customer")
    def test_create_entity_individual_type_with_name_only(
        self, mock_customer_cls, mock_db, import_config, ar_control_account_id
    ):
        """Customer type should be INDIVIDUAL when only first/last name provided."""
        from app.models.finance.ar.customer import CustomerType

        importer = CustomerImporter(mock_db, import_config, ar_control_account_id)
        row = {
            "display_name": "John Smith",
            "company_name": "",
            "first_name": "John",
            "last_name": "Smith",
        }

        importer.create_entity(row)

        call_kwargs = mock_customer_cls.call_args[1]
        assert call_kwargs["customer_type"] == CustomerType.INDIVIDUAL

    def test_code_counter_increments(self, mock_db, import_config, ar_control_account_id):
        """Customer code counter should increment."""
        importer = CustomerImporter(mock_db, import_config, ar_control_account_id)
        assert importer._code_counter == 0
        importer._code_counter += 1
        assert importer._code_counter == 1

    def test_build_address_returns_dict(self, mock_db, import_config, ar_control_account_id):
        """_build_address should return address dict with non-null values."""
        importer = CustomerImporter(mock_db, import_config, ar_control_account_id)
        row = {
            "billing_street": "123 Main St",
            "billing_city": "Lagos",
            "billing_country": "Nigeria",
            "billing_attention": None,
            "billing_street2": None,
            "billing_state": None,
            "billing_postal_code": None,
            "billing_phone": None,
        }

        address = importer._build_address(row, "billing")

        assert address == {
            "street": "123 Main St",
            "city": "Lagos",
            "country": "Nigeria",
        }

    def test_build_address_returns_none_if_empty(
        self, mock_db, import_config, ar_control_account_id
    ):
        """_build_address should return None if all fields are empty."""
        importer = CustomerImporter(mock_db, import_config, ar_control_account_id)
        row = {
            "billing_street": None,
            "billing_city": None,
            "billing_country": None,
            "billing_attention": None,
            "billing_street2": None,
            "billing_state": None,
            "billing_postal_code": None,
            "billing_phone": None,
        }

        address = importer._build_address(row, "billing")
        assert address is None


# ============ Test SupplierImporter ============

class TestSupplierImporter:
    """Tests for SupplierImporter class."""

    def test_entity_name_is_supplier(self, mock_db, import_config, ap_control_account_id):
        """Entity name should be Supplier."""
        importer = SupplierImporter(mock_db, import_config, ap_control_account_id)
        assert importer.entity_name == "Supplier"

    def test_get_unique_key_prefers_display_name(
        self, mock_db, import_config, ap_control_account_id
    ):
        """Unique key should prefer Display Name over Contact Name."""
        importer = SupplierImporter(mock_db, import_config, ap_control_account_id)
        row = {"Display Name": "Vendor Inc", "Contact Name": "John"}
        assert importer.get_unique_key(row) == "Vendor Inc"

    def test_get_unique_key_falls_back_to_contact_name(
        self, mock_db, import_config, ap_control_account_id
    ):
        """Unique key should fall back to Contact Name."""
        importer = SupplierImporter(mock_db, import_config, ap_control_account_id)
        row = {"Display Name": "", "Contact Name": "  John Vendor  "}
        assert importer.get_unique_key(row) == "John Vendor"

    @patch("app.services.ifrs.import_export.contacts.Supplier")
    def test_create_entity_generates_supplier_code(
        self, mock_supplier_cls, mock_db, import_config, ap_control_account_id
    ):
        """create_entity should generate SUPP##### code."""
        importer = SupplierImporter(mock_db, import_config, ap_control_account_id)
        row = {
            "display_name": "Tech Supplies Inc",
            "contact_name": "",
            "company_name": "Tech Supplies Inc",
        }

        importer.create_entity(row)

        call_kwargs = mock_supplier_cls.call_args[1]
        assert call_kwargs["supplier_code"] == "SUPP00001"

    @patch("app.services.ifrs.import_export.contacts.Supplier")
    def test_create_entity_uses_vendor_type_for_company(
        self, mock_supplier_cls, mock_db, import_config, ap_control_account_id
    ):
        """Supplier type should be VENDOR for companies."""
        from app.models.finance.ap.supplier import SupplierType

        importer = SupplierImporter(mock_db, import_config, ap_control_account_id)
        row = {
            "display_name": "Tech Supplies Inc",
            "contact_name": "",
            "company_name": "Tech Supplies Inc",
        }

        importer.create_entity(row)

        call_kwargs = mock_supplier_cls.call_args[1]
        assert call_kwargs["supplier_type"] == SupplierType.VENDOR

    @patch("app.services.ifrs.import_export.contacts.Supplier")
    def test_create_entity_sets_withholding_tax_from_taxable(
        self, mock_supplier_cls, mock_db, import_config, ap_control_account_id
    ):
        """withholding_tax_applicable should be set from taxable field."""
        importer = SupplierImporter(mock_db, import_config, ap_control_account_id)
        row = {
            "display_name": "Tax Vendor",
            "contact_name": "",
            "company_name": "Tax Vendor Ltd",
            "taxable": True,
        }

        importer.create_entity(row)

        call_kwargs = mock_supplier_cls.call_args[1]
        assert call_kwargs["withholding_tax_applicable"] is True

    def test_build_address_remittance(self, mock_db, import_config, ap_control_account_id):
        """_build_address should build remittance address from shipping fields."""
        importer = SupplierImporter(mock_db, import_config, ap_control_account_id)
        row = {
            "remittance_street": "456 Vendor Way",
            "remittance_city": "Abuja",
            "remittance_country": "Nigeria",
            "remittance_attention": None,
            "remittance_street2": None,
            "remittance_state": None,
            "remittance_postal_code": None,
            "remittance_phone": None,
        }

        address = importer._build_address(row, "remittance")

        assert address["street"] == "456 Vendor Way"
        assert address["city"] == "Abuja"


# ============ Test Control Account Helpers ============

class TestControlAccountHelpers:
    """Tests for get_ar_control_account and get_ap_control_account."""

    def test_get_ar_control_account_found(self, organization_id):
        """get_ar_control_account should return account_id when found."""
        mock_db = MagicMock()
        mock_account = MagicMock()
        mock_account.account_id = uuid.uuid4()
        mock_db.execute.return_value.scalar_one_or_none.return_value = mock_account

        result = get_ar_control_account(mock_db, organization_id)

        assert result == mock_account.account_id

    def test_get_ar_control_account_not_found(self, organization_id):
        """get_ar_control_account should return None when not found."""
        mock_db = MagicMock()
        mock_db.execute.return_value.scalar_one_or_none.return_value = None

        result = get_ar_control_account(mock_db, organization_id)

        assert result is None

    def test_get_ap_control_account_found(self, organization_id):
        """get_ap_control_account should return account_id when found."""
        mock_db = MagicMock()
        mock_account = MagicMock()
        mock_account.account_id = uuid.uuid4()
        mock_db.execute.return_value.scalar_one_or_none.return_value = mock_account

        result = get_ap_control_account(mock_db, organization_id)

        assert result == mock_account.account_id

    def test_get_ap_control_account_not_found(self, organization_id):
        """get_ap_control_account should return None when not found."""
        mock_db = MagicMock()
        mock_db.execute.return_value.scalar_one_or_none.return_value = None

        result = get_ap_control_account(mock_db, organization_id)

        assert result is None
