"""
Shared fixtures for IFRS bulk service tests.

Provides mock objects for testing supplier, customer, and account bulk services.
"""

import uuid
from decimal import Decimal
from enum import Enum
from unittest.mock import MagicMock

import pytest


# ============ Mock Enums ============


class MockSupplierType(Enum):
    """Mock supplier type enum."""

    vendor = "vendor"
    contractor = "contractor"
    service_provider = "service_provider"


class MockCustomerType(Enum):
    """Mock customer type enum."""

    individual = "individual"
    company = "company"
    government = "government"


class MockRiskCategory(Enum):
    """Mock risk category enum."""

    low = "low"
    medium = "medium"
    high = "high"


class MockAccountType(Enum):
    """Mock account type enum."""

    posting = "posting"
    control = "control"


class MockAccountCategory(Enum):
    """Mock account category enum."""

    assets = "assets"
    liabilities = "liabilities"
    equity = "equity"
    revenue = "revenue"
    expenses = "expenses"


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
    """Create a mock database session with chained query methods."""
    db = MagicMock()

    # Setup query chaining
    mock_query = MagicMock()
    db.query.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.filter_by.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.offset.return_value = mock_query
    mock_query.all.return_value = []
    mock_query.first.return_value = None
    mock_query.count.return_value = 0

    return db


# ============ Mock Supplier Entity ============


class MockSupplier:
    """Mock Supplier entity for testing."""

    def __init__(
        self,
        supplier_id: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
        supplier_code: str = "SUPP001",
        legal_name: str = "Test Supplier Ltd",
        trading_name: str | None = None,
        supplier_type: MockSupplierType | None = MockSupplierType.vendor,
        tax_identification_number: str | None = None,
        registration_number: str | None = None,
        currency_code: str = "USD",
        payment_terms_days: int = 30,
        is_related_party: bool = False,
        is_active: bool = True,
        primary_contact: dict | None = None,
    ):
        self.supplier_id = supplier_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.UUID(
            "00000000-0000-0000-0000-000000000001"
        )
        self.supplier_code = supplier_code
        self.legal_name = legal_name
        self.trading_name = trading_name
        self.supplier_type = supplier_type
        self.tax_identification_number = tax_identification_number
        self.registration_number = registration_number
        self.currency_code = currency_code
        self.payment_terms_days = payment_terms_days
        self.is_related_party = is_related_party
        self.is_active = is_active
        self.primary_contact = primary_contact or {}


@pytest.fixture
def mock_supplier(organization_id):
    """Create a mock supplier entity."""
    return MockSupplier(
        organization_id=organization_id,
        supplier_code="SUPP001",
        legal_name="Test Supplier Ltd",
        trading_name="Test Trading",
        supplier_type=MockSupplierType.vendor,
        tax_identification_number="TAX123456",
        currency_code="USD",
        payment_terms_days=30,
        primary_contact={
            "name": "John Doe",
            "email": "john@supplier.com",
            "phone": "+1-555-0100",
        },
    )


@pytest.fixture
def mock_supplier_with_invoices(mock_supplier):
    """Create a mock supplier that has invoices."""
    return mock_supplier


# ============ Mock Customer Entity ============


class MockCustomer:
    """Mock Customer entity for testing."""

    def __init__(
        self,
        customer_id: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
        customer_code: str = "CUST001",
        legal_name: str = "Test Customer Ltd",
        trading_name: str | None = None,
        customer_type: MockCustomerType | None = MockCustomerType.company,
        tax_identification_number: str | None = None,
        registration_number: str | None = None,
        currency_code: str = "USD",
        credit_limit: Decimal = Decimal("10000.00"),
        credit_terms_days: int = 30,
        credit_hold: bool = False,
        risk_category: MockRiskCategory | None = MockRiskCategory.low,
        is_related_party: bool = False,
        is_active: bool = True,
        primary_contact: dict | None = None,
    ):
        self.customer_id = customer_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.UUID(
            "00000000-0000-0000-0000-000000000001"
        )
        self.customer_code = customer_code
        self.legal_name = legal_name
        self.trading_name = trading_name
        self.customer_type = customer_type
        self.tax_identification_number = tax_identification_number
        self.registration_number = registration_number
        self.currency_code = currency_code
        self.credit_limit = credit_limit
        self.credit_terms_days = credit_terms_days
        self.credit_hold = credit_hold
        self.risk_category = risk_category
        self.is_related_party = is_related_party
        self.is_active = is_active
        self.primary_contact = primary_contact or {}


@pytest.fixture
def mock_customer(organization_id):
    """Create a mock customer entity."""
    return MockCustomer(
        organization_id=organization_id,
        customer_code="CUST001",
        legal_name="Test Customer Ltd",
        trading_name="Test Trading Co",
        customer_type=MockCustomerType.company,
        tax_identification_number="TAX789012",
        currency_code="USD",
        credit_limit=Decimal("50000.00"),
        credit_terms_days=30,
        credit_hold=False,
        risk_category=MockRiskCategory.low,
        primary_contact={
            "name": "Jane Smith",
            "email": "jane@customer.com",
            "phone": "+1-555-0200",
        },
    )


# ============ Mock Account Entity ============


class MockAccount:
    """Mock Account entity for testing."""

    def __init__(
        self,
        account_id: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
        account_code: str = "1000",
        account_name: str = "Test Account",
        account_type: MockAccountType | None = MockAccountType.posting,
        account_category: MockAccountCategory | None = MockAccountCategory.assets,
        is_control_account: bool = False,
        currency_code: str = "USD",
        is_active: bool = True,
        description: str | None = None,
    ):
        self.account_id = account_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.UUID(
            "00000000-0000-0000-0000-000000000001"
        )
        self.account_code = account_code
        self.account_name = account_name
        self.account_type = account_type
        self.account_category = account_category
        self.is_control_account = is_control_account
        self.currency_code = currency_code
        self.is_active = is_active
        self.description = description


@pytest.fixture
def mock_account(organization_id):
    """Create a mock account entity."""
    return MockAccount(
        organization_id=organization_id,
        account_code="1000",
        account_name="Cash",
        account_type=MockAccountType.posting,
        account_category=MockAccountCategory.assets,
        is_control_account=False,
        currency_code="USD",
        description="Cash at bank",
    )


@pytest.fixture
def mock_control_account(organization_id):
    """Create a mock control account entity."""
    return MockAccount(
        organization_id=organization_id,
        account_code="1200",
        account_name="Accounts Receivable Control",
        account_type=MockAccountType.control,
        account_category=MockAccountCategory.assets,
        is_control_account=True,
        currency_code="USD",
        description="AR control account",
    )
