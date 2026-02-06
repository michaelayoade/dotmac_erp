"""
Fixtures for Tax module tests.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from unittest.mock import MagicMock

import pytest

# Import actual enums
from app.models.finance.tax.tax_code import TaxType


class MockTaxJurisdiction:
    """Mock TaxJurisdiction model."""

    def __init__(
        self,
        jurisdiction_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        jurisdiction_code: str = "US-CA",
        jurisdiction_name: str = "California",
        country_code: str = "US",
        jurisdiction_level: str = "STATE",
        current_tax_rate: Decimal = Decimal("8.25"),
        tax_rate_effective_from: date = None,
        currency_code: str = "USD",
        current_tax_payable_account_id: uuid.UUID = None,
        current_tax_expense_account_id: uuid.UUID = None,
        deferred_tax_asset_account_id: uuid.UUID = None,
        deferred_tax_liability_account_id: uuid.UUID = None,
        deferred_tax_expense_account_id: uuid.UUID = None,
        is_active: bool = True,
        **kwargs,
    ):
        self.jurisdiction_id = jurisdiction_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.jurisdiction_code = jurisdiction_code
        self.jurisdiction_name = jurisdiction_name
        self.country_code = country_code
        self.jurisdiction_level = jurisdiction_level
        self.current_tax_rate = current_tax_rate
        self.tax_rate_effective_from = tax_rate_effective_from or date.today()
        self.currency_code = currency_code
        self.current_tax_payable_account_id = (
            current_tax_payable_account_id or uuid.uuid4()
        )
        self.current_tax_expense_account_id = (
            current_tax_expense_account_id or uuid.uuid4()
        )
        self.deferred_tax_asset_account_id = (
            deferred_tax_asset_account_id or uuid.uuid4()
        )
        self.deferred_tax_liability_account_id = (
            deferred_tax_liability_account_id or uuid.uuid4()
        )
        self.deferred_tax_expense_account_id = (
            deferred_tax_expense_account_id or uuid.uuid4()
        )
        self.is_active = is_active
        self.created_at = datetime.now(timezone.utc)
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockTaxCode:
    """Mock TaxCode model."""

    def __init__(
        self,
        tax_code_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        tax_code: str = "VAT20",
        tax_name: str = "VAT 20%",
        tax_type: TaxType = TaxType.VAT,
        jurisdiction_id: uuid.UUID = None,
        tax_rate: Decimal = Decimal("0.20"),
        effective_from: date = None,
        effective_to: Optional[date] = None,
        is_compound: bool = False,
        is_inclusive: bool = False,
        is_recoverable: bool = True,
        recovery_rate: Decimal = Decimal("1.0"),
        applies_to_purchases: bool = True,
        applies_to_sales: bool = True,
        is_active: bool = True,
        **kwargs,
    ):
        self.tax_code_id = tax_code_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.tax_code = tax_code
        self.tax_name = tax_name
        self.tax_type = tax_type
        self.jurisdiction_id = jurisdiction_id or uuid.uuid4()
        self.tax_rate = tax_rate
        self.effective_from = effective_from or date(2024, 1, 1)
        self.effective_to = effective_to
        self.is_compound = is_compound
        self.is_inclusive = is_inclusive
        self.is_recoverable = is_recoverable
        self.recovery_rate = recovery_rate
        self.applies_to_purchases = applies_to_purchases
        self.applies_to_sales = applies_to_sales
        self.is_active = is_active
        self.created_at = datetime.now(timezone.utc)
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockTaxPeriod:
    """Mock TaxPeriod model."""

    def __init__(
        self,
        period_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        jurisdiction_id: uuid.UUID = None,
        period_code: str = "2024Q1",
        period_name: str = "Q1 2024",
        period_start: date = None,
        period_end: date = None,
        filing_due_date: date = None,
        status: str = "OPEN",
        is_closed: bool = False,
        **kwargs,
    ):
        self.period_id = period_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.jurisdiction_id = jurisdiction_id or uuid.uuid4()
        self.period_code = period_code
        self.period_name = period_name
        self.period_start = period_start or date(2024, 1, 1)
        self.period_end = period_end or date(2024, 3, 31)
        self.filing_due_date = filing_due_date or date(2024, 4, 30)
        self.status = status
        self.is_closed = is_closed
        self.created_at = datetime.now(timezone.utc)
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockTaxTransaction:
    """Mock TaxTransaction model."""

    def __init__(
        self,
        transaction_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        tax_code_id: uuid.UUID = None,
        tax_period_id: uuid.UUID = None,
        transaction_date: date = None,
        transaction_type: str = "SALES",
        base_amount: Decimal = Decimal("1000.00"),
        tax_amount: Decimal = Decimal("200.00"),
        total_amount: Decimal = Decimal("1200.00"),
        source_module: str = "AR",
        source_document_type: str = "INVOICE",
        source_document_id: uuid.UUID = None,
        is_posted: bool = False,
        **kwargs,
    ):
        self.transaction_id = transaction_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.tax_code_id = tax_code_id or uuid.uuid4()
        self.tax_period_id = tax_period_id or uuid.uuid4()
        self.transaction_date = transaction_date or date.today()
        self.transaction_type = transaction_type
        self.base_amount = base_amount
        self.tax_amount = tax_amount
        self.total_amount = total_amount
        self.source_module = source_module
        self.source_document_type = source_document_type
        self.source_document_id = source_document_id or uuid.uuid4()
        self.is_posted = is_posted
        self.created_at = datetime.now(timezone.utc)
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockTaxReturn:
    """Mock TaxReturn model."""

    def __init__(
        self,
        return_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        jurisdiction_id: uuid.UUID = None,
        tax_period_id: uuid.UUID = None,
        return_type: str = "VAT_RETURN",
        filing_date: Optional[date] = None,
        due_date: date = None,
        status: str = "DRAFT",
        total_output_tax: Decimal = Decimal("0"),
        total_input_tax: Decimal = Decimal("0"),
        net_tax_payable: Decimal = Decimal("0"),
        **kwargs,
    ):
        self.return_id = return_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.jurisdiction_id = jurisdiction_id or uuid.uuid4()
        self.tax_period_id = tax_period_id or uuid.uuid4()
        self.return_type = return_type
        self.filing_date = filing_date
        self.due_date = due_date or date.today()
        self.status = status
        self.total_output_tax = total_output_tax
        self.total_input_tax = total_input_tax
        self.net_tax_payable = net_tax_payable
        self.created_at = datetime.now(timezone.utc)
        for k, v in kwargs.items():
            setattr(self, k, v)


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    db = MagicMock()
    db.get = MagicMock(return_value=None)
    return db


@pytest.fixture
def org_id() -> uuid.UUID:
    """Generate a test organization ID."""
    return uuid.uuid4()


@pytest.fixture
def user_id() -> uuid.UUID:
    """Generate a test user ID."""
    return uuid.uuid4()


@pytest.fixture
def mock_jurisdiction(org_id) -> MockTaxJurisdiction:
    """Create a mock tax jurisdiction."""
    return MockTaxJurisdiction(organization_id=org_id)


@pytest.fixture
def mock_tax_code(org_id, mock_jurisdiction) -> MockTaxCode:
    """Create a mock tax code."""
    return MockTaxCode(
        organization_id=org_id,
        jurisdiction_id=mock_jurisdiction.jurisdiction_id,
    )


@pytest.fixture
def mock_tax_period(org_id, mock_jurisdiction) -> MockTaxPeriod:
    """Create a mock tax period."""
    return MockTaxPeriod(
        organization_id=org_id,
        jurisdiction_id=mock_jurisdiction.jurisdiction_id,
    )


@pytest.fixture
def mock_tax_transaction(org_id, mock_tax_code, mock_tax_period) -> MockTaxTransaction:
    """Create a mock tax transaction."""
    return MockTaxTransaction(
        organization_id=org_id,
        tax_code_id=mock_tax_code.tax_code_id,
        tax_period_id=mock_tax_period.period_id,
    )


@pytest.fixture
def mock_tax_return(org_id, mock_jurisdiction, mock_tax_period) -> MockTaxReturn:
    """Create a mock tax return."""
    return MockTaxReturn(
        organization_id=org_id,
        jurisdiction_id=mock_jurisdiction.jurisdiction_id,
        tax_period_id=mock_tax_period.period_id,
    )
