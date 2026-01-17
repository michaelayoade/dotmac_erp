"""
Shared fixtures for import/export service tests.

Provides mock objects, sample CSV data, and helper classes for testing
the import framework including base importer, validation rules, and
entity-specific importers.
"""

import csv
import io
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest


# ============ UUID Fixtures ============

@pytest.fixture
def organization_id():
    """Provide a consistent organization UUID for tests."""
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def user_id():
    """Provide a consistent user UUID for tests."""
    return uuid.UUID("00000000-0000-0000-0000-000000000002")


# ============ Mock Database Session ============

@pytest.fixture
def mock_db():
    """Create a mock database session."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.execute.return_value.scalar_one_or_none.return_value = None
    return db


# ============ Import Config Fixture ============

@pytest.fixture
def import_config(organization_id, user_id):
    """Create a default import config."""
    from app.services.ifrs.import_export.base import ImportConfig

    return ImportConfig(
        organization_id=organization_id,
        user_id=user_id,
        skip_duplicates=True,
        dry_run=False,
        batch_size=100,
        stop_on_error=False,
        date_format="%Y-%m-%d",
        encoding="utf-8",
    )


# ============ CSV File Helpers ============

class CSVFileHelper:
    """Helper class for creating temporary CSV files for testing."""

    @staticmethod
    def create_csv_file(
        headers: List[str],
        rows: List[List[str]],
        encoding: str = "utf-8",
    ) -> Path:
        """Create a temporary CSV file with the given data."""
        temp_file = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".csv",
            delete=False,
            encoding=encoding,
            newline="",
        )
        writer = csv.writer(temp_file)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)
        temp_file.close()
        return Path(temp_file.name)

    @staticmethod
    def create_csv_string(headers: List[str], rows: List[List[str]]) -> str:
        """Create a CSV string with the given data."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)
        return output.getvalue()

    @staticmethod
    def create_dict_rows(headers: List[str], rows: List[List[str]]) -> List[Dict[str, str]]:
        """Convert headers and rows to list of dictionaries."""
        return [dict(zip(headers, row)) for row in rows]


@pytest.fixture
def csv_helper():
    """Provide CSV file helper."""
    return CSVFileHelper()


# ============ Sample Account CSV Data ============

@pytest.fixture
def sample_account_headers():
    """Standard account CSV headers (Zoho format)."""
    return [
        "Account Name",
        "Account Code",
        "Account Type",
        "Description",
        "Currency",
        "Is Active",
    ]


@pytest.fixture
def sample_account_rows():
    """Sample account data rows."""
    return [
        ["Cash at Bank", "1000", "Bank", "Main checking account", "USD", "Yes"],
        ["Accounts Receivable", "1200", "Accounts Receivable", "Trade receivables", "USD", "Yes"],
        ["Revenue", "4000", "Income", "Sales revenue", "USD", "Yes"],
        ["Office Supplies", "5100", "Expense", "Office supplies expense", "USD", "Yes"],
    ]


@pytest.fixture
def sample_account_csv(csv_helper, sample_account_headers, sample_account_rows):
    """Create a temporary CSV file with account data."""
    return csv_helper.create_csv_file(sample_account_headers, sample_account_rows)


# ============ Sample Contact CSV Data ============

@pytest.fixture
def sample_customer_headers():
    """Standard customer CSV headers."""
    return [
        "Display Name",
        "Company Name",
        "Email",
        "Phone",
        "Billing Address",
        "Currency",
    ]


@pytest.fixture
def sample_customer_rows():
    """Sample customer data rows."""
    return [
        ["John Smith", "Smith Industries", "john@smith.com", "+1-555-0100", "123 Main St", "USD"],
        ["ABC Corporation", "ABC Corp", "contact@abc.com", "+1-555-0200", "456 Oak Ave", "USD"],
        ["Jane Doe", "", "jane@example.com", "+1-555-0300", "789 Pine Rd", "EUR"],
    ]


@pytest.fixture
def sample_customer_csv(csv_helper, sample_customer_headers, sample_customer_rows):
    """Create a temporary CSV file with customer data."""
    return csv_helper.create_csv_file(sample_customer_headers, sample_customer_rows)


@pytest.fixture
def sample_supplier_headers():
    """Standard supplier CSV headers."""
    return [
        "Vendor Name",
        "Company Name",
        "Email",
        "Phone",
        "Address",
        "Currency",
        "Payment Terms",
    ]


@pytest.fixture
def sample_supplier_rows():
    """Sample supplier data rows."""
    return [
        ["Office Depot", "Office Depot Inc", "orders@officedepot.com", "+1-555-1000", "100 Supplier Way", "USD", "30"],
        ["Tech Solutions", "Tech Solutions LLC", "sales@techsol.com", "+1-555-2000", "200 Tech Park", "USD", "45"],
    ]


@pytest.fixture
def sample_supplier_csv(csv_helper, sample_supplier_headers, sample_supplier_rows):
    """Create a temporary CSV file with supplier data."""
    return csv_helper.create_csv_file(sample_supplier_headers, sample_supplier_rows)


# ============ QuickBooks Format CSV Data ============

@pytest.fixture
def quickbooks_account_headers():
    """QuickBooks format account headers."""
    return [
        "FullyQualifiedName",
        "AcctNum",
        "AccountType",
        "Classification",
        "CurrentBalance",
        "Active",
    ]


@pytest.fixture
def quickbooks_account_rows():
    """QuickBooks format account data."""
    return [
        ["Checking", "1000", "Bank", "Asset", "5000.00", "true"],
        ["Accounts Receivable", "1200", "Accounts Receivable", "Asset", "10000.00", "true"],
        ["Sales", "4000", "Income", "Revenue", "0.00", "true"],
    ]


@pytest.fixture
def quickbooks_account_csv(csv_helper, quickbooks_account_headers, quickbooks_account_rows):
    """Create a QuickBooks format account CSV."""
    return csv_helper.create_csv_file(quickbooks_account_headers, quickbooks_account_rows)


# ============ Xero Format CSV Data ============

@pytest.fixture
def xero_account_headers():
    """Xero format account headers."""
    return [
        "*Code",
        "*Name",
        "*Type",
        "Description",
        "Tax Code",
    ]


@pytest.fixture
def xero_account_rows():
    """Xero format account data."""
    return [
        ["1000", "Bank Account", "BANK", "Main bank account", ""],
        ["1100", "Accounts Receivable", "CURRENT", "Trade debtors", ""],
        ["2000", "Accounts Payable", "CURRENT LIABILITY", "Trade creditors", ""],
    ]


@pytest.fixture
def xero_account_csv(csv_helper, xero_account_headers, xero_account_rows):
    """Create a Xero format account CSV."""
    return csv_helper.create_csv_file(xero_account_headers, xero_account_rows)


# ============ Invalid CSV Data ============

@pytest.fixture
def invalid_csv_missing_required(csv_helper):
    """CSV with missing required fields."""
    headers = ["Account Name", "Description"]  # Missing Account Code
    rows = [
        ["Cash", "Cash account"],
        ["Revenue", "Revenue account"],
    ]
    return csv_helper.create_csv_file(headers, rows)


@pytest.fixture
def invalid_csv_bad_email(csv_helper):
    """CSV with invalid email addresses."""
    headers = ["Display Name", "Email", "Phone"]
    rows = [
        ["John Smith", "not-an-email", "+1-555-0100"],
        ["Jane Doe", "invalid@", "+1-555-0200"],
    ]
    return csv_helper.create_csv_file(headers, rows)


@pytest.fixture
def empty_csv(csv_helper):
    """CSV with only headers, no data."""
    headers = ["Account Name", "Account Code", "Account Type"]
    return csv_helper.create_csv_file(headers, [])


# ============ Mock Entity Classes ============

@dataclass
class MockAccount:
    """Mock Account entity for testing."""
    account_id: uuid.UUID = field(default_factory=uuid.uuid4)
    organization_id: uuid.UUID = field(default_factory=lambda: uuid.UUID("00000000-0000-0000-0000-000000000001"))
    account_code: str = "1000"
    account_name: str = "Test Account"
    account_type: str = "bank"
    account_category: str = "assets"
    description: str = ""
    currency_code: str = "USD"
    is_active: bool = True


@dataclass
class MockCustomer:
    """Mock Customer entity for testing."""
    customer_id: uuid.UUID = field(default_factory=uuid.uuid4)
    organization_id: uuid.UUID = field(default_factory=lambda: uuid.UUID("00000000-0000-0000-0000-000000000001"))
    customer_code: str = "CUST001"
    legal_name: str = "Test Customer"
    trading_name: str = ""
    email: str = ""
    phone: str = ""
    currency_code: str = "USD"
    is_active: bool = True


@dataclass
class MockSupplier:
    """Mock Supplier entity for testing."""
    supplier_id: uuid.UUID = field(default_factory=uuid.uuid4)
    organization_id: uuid.UUID = field(default_factory=lambda: uuid.UUID("00000000-0000-0000-0000-000000000001"))
    supplier_code: str = "SUPP001"
    legal_name: str = "Test Supplier"
    trading_name: str = ""
    email: str = ""
    phone: str = ""
    currency_code: str = "USD"
    payment_terms_days: int = 30
    is_active: bool = True


# ============ Concrete Test Importer ============

class ConcreteTestImporter:
    """
    Concrete implementation of BaseImporter for testing base class functionality.

    This is used to test the abstract base class methods without relying on
    actual entity importers.
    """

    def __init__(self, db, config, duplicates=None):
        from app.services.ifrs.import_export.base import BaseImporter, FieldMapping

        self._duplicates = duplicates or set()

        class TestImporter(BaseImporter[MockAccount]):
            entity_name = "TestEntity"
            model_class = MockAccount

            def __init__(inner_self, db, config):
                super().__init__(db, config)
                inner_self._duplicates = duplicates or set()

            def get_field_mappings(inner_self):
                return [
                    FieldMapping("Account Name", "account_name", required=True),
                    FieldMapping("Account Code", "account_code", required=True),
                    FieldMapping("Account Type", "account_type", required=False),
                    FieldMapping("Description", "description", required=False),
                    FieldMapping("Currency", "currency_code", required=False, default="USD"),
                ]

            def get_unique_key(inner_self, row):
                return row.get("Account Code", "")

            def check_duplicate(inner_self, row):
                key = inner_self.get_unique_key(row)
                return key in inner_self._duplicates

            def create_entity(inner_self, row):
                return MockAccount(
                    account_name=row.get("account_name", ""),
                    account_code=row.get("account_code", ""),
                    account_type=row.get("account_type", ""),
                    description=row.get("description", ""),
                    currency_code=row.get("currency_code", "USD"),
                )

        self.importer = TestImporter(db, config)

    def __getattr__(self, name):
        return getattr(self.importer, name)


@pytest.fixture
def test_importer(mock_db, import_config):
    """Create a concrete test importer instance."""
    return ConcreteTestImporter(mock_db, import_config)
