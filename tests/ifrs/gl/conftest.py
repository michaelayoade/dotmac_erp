"""
Fixtures for GL Services Tests.

Shared mock objects for GL module testing.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

# ============ Mock Enums ============
from app.models.finance.gl.account import AccountType, NormalBalance
from app.models.finance.gl.fiscal_period import PeriodStatus
from app.models.finance.gl.journal_entry import JournalStatus, JournalType

MockAccountType = AccountType
MockNormalBalance = NormalBalance
MockPeriodStatus = PeriodStatus
MockJournalStatus = JournalStatus
MockJournalType = JournalType


# ============ Mock Model Classes ============


class MockAccount:
    """Mock Account model."""

    def __init__(
        self,
        account_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        category_id: uuid.UUID = None,
        account_code: str = "1000",
        account_name: str = "Cash",
        description: str | None = None,
        search_terms: str | None = None,
        account_type: AccountType = None,
        normal_balance: NormalBalance = None,
        is_multi_currency: bool = False,
        default_currency_code: str | None = "USD",
        is_active: bool = True,
        is_posting_allowed: bool = True,
        is_budgetable: bool = True,
        is_reconciliation_required: bool = False,
        subledger_type: str | None = None,
        is_cash_equivalent: bool = False,
        is_financial_instrument: bool = False,
        created_at: datetime = None,
        updated_at: datetime = None,
    ):
        self.account_id = account_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.category_id = category_id or uuid.uuid4()
        self.account_code = account_code
        self.account_name = account_name
        self.description = description
        self.search_terms = search_terms
        self.account_type = account_type or MockAccountType.POSTING
        self.normal_balance = normal_balance or MockNormalBalance.DEBIT
        self.is_multi_currency = is_multi_currency
        self.default_currency_code = default_currency_code
        self.is_active = is_active
        self.is_posting_allowed = is_posting_allowed
        self.is_budgetable = is_budgetable
        self.is_reconciliation_required = is_reconciliation_required
        self.subledger_type = subledger_type
        self.is_cash_equivalent = is_cash_equivalent
        self.is_financial_instrument = is_financial_instrument
        self.created_at = created_at or datetime.now(UTC)
        self.updated_at = updated_at


class MockFiscalYear:
    """Mock FiscalYear model."""

    def __init__(
        self,
        fiscal_year_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        year_code: str = "FY2024",
        year_name: str = "Fiscal Year 2024",
        start_date: date = None,
        end_date: date = None,
        is_adjustment_year: bool = False,
        is_closed: bool = False,
        closed_at: datetime = None,
        closed_by_user_id: uuid.UUID = None,
        retained_earnings_account_id: uuid.UUID = None,
        created_at: datetime = None,
    ):
        self.fiscal_year_id = fiscal_year_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.year_code = year_code
        self.year_name = year_name
        self.start_date = start_date or date(2024, 1, 1)
        self.end_date = end_date or date(2024, 12, 31)
        self.is_adjustment_year = is_adjustment_year
        self.is_closed = is_closed
        self.closed_at = closed_at
        self.closed_by_user_id = closed_by_user_id
        self.retained_earnings_account_id = retained_earnings_account_id
        self.created_at = created_at or datetime.now(UTC)


class MockFiscalPeriod:
    """Mock FiscalPeriod model."""

    def __init__(
        self,
        fiscal_period_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        fiscal_year_id: uuid.UUID = None,
        period_number: int = 1,
        period_name: str = "January 2024",
        start_date: date = None,
        end_date: date = None,
        is_adjustment_period: bool = False,
        is_closing_period: bool = False,
        status: PeriodStatus = None,
        soft_closed_at: datetime = None,
        soft_closed_by_user_id: uuid.UUID = None,
        hard_closed_at: datetime = None,
        hard_closed_by_user_id: uuid.UUID = None,
        reopen_count: int = 0,
        last_reopen_session_id: uuid.UUID = None,
        created_at: datetime = None,
    ):
        self.fiscal_period_id = fiscal_period_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.fiscal_year_id = fiscal_year_id or uuid.uuid4()
        self.period_number = period_number
        self.period_name = period_name
        self.start_date = start_date or date(2024, 1, 1)
        self.end_date = end_date or date(2024, 1, 31)
        self.is_adjustment_period = is_adjustment_period
        self.is_closing_period = is_closing_period
        self.status = status or MockPeriodStatus.FUTURE
        self.soft_closed_at = soft_closed_at
        self.soft_closed_by_user_id = soft_closed_by_user_id
        self.hard_closed_at = hard_closed_at
        self.hard_closed_by_user_id = hard_closed_by_user_id
        self.reopen_count = reopen_count
        self.last_reopen_session_id = last_reopen_session_id
        self.created_at = created_at or datetime.now(UTC)


class MockJournalEntry:
    """Mock JournalEntry model."""

    def __init__(
        self,
        journal_entry_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        journal_number: str = "JE-0001",
        journal_type: JournalType = None,
        entry_date: date = None,
        posting_date: date = None,
        fiscal_period_id: uuid.UUID = None,
        description: str = "Test journal",
        reference: str | None = None,
        currency_code: str = "USD",
        exchange_rate: Decimal = Decimal("1.0"),
        total_debit: Decimal = Decimal("0"),
        total_credit: Decimal = Decimal("0"),
        total_debit_functional: Decimal = Decimal("0"),
        total_credit_functional: Decimal = Decimal("0"),
        status: JournalStatus = None,
        created_by_user_id: uuid.UUID = None,
        submitted_by_user_id: uuid.UUID = None,
        approved_by_user_id: uuid.UUID = None,
        posted_by_user_id: uuid.UUID = None,
        created_at: datetime = None,
    ):
        self.journal_entry_id = journal_entry_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.journal_number = journal_number
        self.journal_type = journal_type or MockJournalType.STANDARD
        self.entry_date = entry_date or date.today()
        self.posting_date = posting_date or date.today()
        self.fiscal_period_id = fiscal_period_id or uuid.uuid4()
        self.description = description
        self.reference = reference
        self.currency_code = currency_code
        self.exchange_rate = exchange_rate
        self.total_debit = total_debit
        self.total_credit = total_credit
        self.total_debit_functional = total_debit_functional
        self.total_credit_functional = total_credit_functional
        self.status = status or MockJournalStatus.DRAFT
        self.created_by_user_id = created_by_user_id or uuid.uuid4()
        self.submitted_by_user_id = submitted_by_user_id
        self.approved_by_user_id = approved_by_user_id
        self.posted_by_user_id = posted_by_user_id
        self.created_at = created_at or datetime.now(UTC)


# ============ Fixtures ============


@pytest.fixture
def org_id() -> uuid.UUID:
    """Generate a test organization ID."""
    return uuid.uuid4()


@pytest.fixture
def user_id() -> uuid.UUID:
    """Generate a test user ID."""
    return uuid.uuid4()


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    session = MagicMock()
    session.query = MagicMock(return_value=session)
    session.filter = MagicMock(return_value=session)
    session.first = MagicMock(return_value=None)
    session.all = MagicMock(return_value=[])
    session.count = MagicMock(return_value=0)
    session.add = MagicMock()
    session.commit = MagicMock()
    session.flush = MagicMock()
    session.refresh = MagicMock()
    session.delete = MagicMock()
    session.get = MagicMock(return_value=None)
    session.execute = MagicMock()
    return session


@pytest.fixture
def mock_account(org_id) -> MockAccount:
    """Create a mock account."""
    return MockAccount(organization_id=org_id)


@pytest.fixture
def mock_fiscal_year(org_id) -> MockFiscalYear:
    """Create a mock fiscal year."""
    return MockFiscalYear(organization_id=org_id)


@pytest.fixture
def mock_fiscal_period(org_id, mock_fiscal_year) -> MockFiscalPeriod:
    """Create a mock fiscal period."""
    return MockFiscalPeriod(
        organization_id=org_id,
        fiscal_year_id=mock_fiscal_year.fiscal_year_id,
    )


@pytest.fixture
def mock_journal_entry(org_id, mock_fiscal_period) -> MockJournalEntry:
    """Create a mock journal entry."""
    return MockJournalEntry(
        organization_id=org_id,
        fiscal_period_id=mock_fiscal_period.fiscal_period_id,
    )
