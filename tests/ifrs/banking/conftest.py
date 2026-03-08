"""
Fixtures for Banking Services Tests.

These tests use mock objects to avoid PostgreSQL-specific dependencies
while still testing the service logic.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

# ============ Mock Enums ============
from app.models.finance.banking.bank_account import BankAccountStatus, BankAccountType
from app.models.finance.banking.bank_reconciliation import (
    ReconciliationMatchType,
    ReconciliationStatus,
)
from app.models.finance.banking.bank_statement import (
    BankStatementStatus,
    StatementLineType,
)

MockBankAccountStatus = BankAccountStatus
MockBankAccountType = BankAccountType
MockBankStatementStatus = BankStatementStatus
MockStatementLineType = StatementLineType
MockReconciliationStatus = ReconciliationStatus
MockReconciliationMatchType = ReconciliationMatchType


# ============ Mock Model Classes ============


class MockBankAccount:
    """Mock BankAccount model."""

    def __init__(
        self,
        bank_account_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        bank_name: str = "Test Bank",
        bank_code: str | None = "TESTBANK",
        branch_code: str | None = None,
        branch_name: str | None = None,
        account_number: str = "1234567890",
        account_name: str = "Main Checking",
        account_type: BankAccountType = BankAccountType.checking,
        iban: str | None = None,
        currency_code: str = "USD",
        gl_account_id: uuid.UUID = None,
        status: BankAccountStatus = BankAccountStatus.active,
        is_primary: bool = False,
        allow_overdraft: bool = False,
        overdraft_limit: Decimal | None = None,
        last_statement_date: date | None = None,
        last_statement_balance: Decimal | None = None,
        last_reconciled_date: date | None = None,
        last_reconciled_balance: Decimal | None = None,
        contact_name: str | None = None,
        contact_phone: str | None = None,
        contact_email: str | None = None,
        notes: str | None = None,
        created_by: uuid.UUID | None = None,
        updated_by: uuid.UUID | None = None,
        created_at: datetime = None,
        updated_at: datetime = None,
    ):
        self.bank_account_id = bank_account_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.bank_name = bank_name
        self.bank_code = bank_code
        self.branch_code = branch_code
        self.branch_name = branch_name
        self.account_number = account_number
        self.account_name = account_name
        self.account_type = account_type
        self.iban = iban
        self.currency_code = currency_code
        self.gl_account_id = gl_account_id or uuid.uuid4()
        self.status = status
        self.is_primary = is_primary
        self.allow_overdraft = allow_overdraft
        self.overdraft_limit = overdraft_limit
        self.last_statement_date = last_statement_date
        self.last_statement_balance = last_statement_balance
        self.last_reconciled_date = last_reconciled_date
        self.last_reconciled_balance = last_reconciled_balance
        self.contact_name = contact_name
        self.contact_phone = contact_phone
        self.contact_email = contact_email
        self.notes = notes
        self.created_by = created_by
        self.updated_by = updated_by
        self.created_at = created_at or datetime.now(UTC)
        self.updated_at = updated_at


class MockGLAccount:
    """Mock GL Account model."""

    def __init__(
        self,
        account_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        account_code: str = "1000",
        account_name: str = "Cash and Cash Equivalents",
        account_type: str = "asset",
        is_active: bool = True,
    ):
        self.account_id = account_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.account_code = account_code
        self.account_name = account_name
        self.account_type = account_type
        self.is_active = is_active


class MockBankStatement:
    """Mock BankStatement model."""

    def __init__(
        self,
        statement_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        bank_account_id: uuid.UUID = None,
        statement_number: str = "STMT-001",
        statement_date: date = None,
        period_start: date = None,
        period_end: date = None,
        opening_balance: Decimal = Decimal("1000.00"),
        closing_balance: Decimal = Decimal("1500.00"),
        total_credits: Decimal = Decimal("600.00"),
        total_debits: Decimal = Decimal("100.00"),
        currency_code: str = "USD",
        status: BankStatementStatus = BankStatementStatus.imported,
        total_lines: int = 5,
        matched_lines: int = 0,
        unmatched_lines: int = 5,
        import_source: str | None = None,
        import_filename: str | None = None,
        imported_by: uuid.UUID | None = None,
        created_at: datetime = None,
    ):
        self.statement_id = statement_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.bank_account_id = bank_account_id or uuid.uuid4()
        self.statement_number = statement_number
        self.statement_date = statement_date or date.today()
        self.period_start = period_start or date.today().replace(day=1)
        self.period_end = period_end or date.today()
        self.opening_balance = opening_balance
        self.closing_balance = closing_balance
        self.total_credits = total_credits
        self.total_debits = total_debits
        self.currency_code = currency_code
        self.status = status
        self.total_lines = total_lines
        self.matched_lines = matched_lines
        self.unmatched_lines = unmatched_lines
        self.import_source = import_source
        self.import_filename = import_filename
        self.imported_by = imported_by
        self.created_at = created_at or datetime.now(UTC)
        self.lines = []

    @property
    def is_balanced(self) -> bool:
        return (
            self.opening_balance + self.total_credits - self.total_debits
            == self.closing_balance
        )


class MockBankStatementLine:
    """Mock BankStatementLine model."""

    def __init__(
        self,
        line_id: uuid.UUID = None,
        statement_id: uuid.UUID = None,
        line_number: int = 1,
        transaction_id: str | None = None,
        transaction_date: date = None,
        value_date: date | None = None,
        transaction_type: StatementLineType = StatementLineType.credit,
        amount: Decimal = Decimal("100.00"),
        running_balance: Decimal | None = None,
        description: str | None = "Test transaction",
        reference: str | None = None,
        payee_payer: str | None = None,
        bank_reference: str | None = None,
        check_number: str | None = None,
        bank_category: str | None = None,
        bank_code: str | None = None,
        is_matched: bool = False,
        matched_at: datetime | None = None,
        matched_by: uuid.UUID | None = None,
        matched_journal_line_id: uuid.UUID | None = None,
        raw_data: dict | None = None,
    ):
        self.line_id = line_id or uuid.uuid4()
        self.statement_id = statement_id or uuid.uuid4()
        self.line_number = line_number
        self.transaction_id = transaction_id
        self.transaction_date = transaction_date or date.today()
        self.value_date = value_date
        self.transaction_type = transaction_type
        self.amount = amount
        self.running_balance = running_balance
        self.description = description
        self.reference = reference
        self.payee_payer = payee_payer
        self.bank_reference = bank_reference
        self.check_number = check_number
        self.bank_category = bank_category
        self.bank_code = bank_code
        self.is_matched = is_matched
        self.matched_at = matched_at
        self.matched_by = matched_by
        self.matched_journal_line_id = matched_journal_line_id
        self.raw_data = raw_data
        self.statement = None  # Reference to parent statement

    @property
    def signed_amount(self) -> Decimal:
        if self.transaction_type == StatementLineType.credit:
            return self.amount
        return -self.amount


class MockBankReconciliation:
    """Mock BankReconciliation model."""

    def __init__(
        self,
        reconciliation_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        bank_account_id: uuid.UUID = None,
        reconciliation_date: date = None,
        period_start: date = None,
        period_end: date = None,
        statement_opening_balance: Decimal = Decimal("1000.00"),
        statement_closing_balance: Decimal = Decimal("1500.00"),
        gl_opening_balance: Decimal = Decimal("1000.00"),
        gl_closing_balance: Decimal = Decimal("1500.00"),
        currency_code: str = "USD",
        status: ReconciliationStatus = ReconciliationStatus.draft,
        total_matched: Decimal = Decimal("0"),
        total_adjustments: Decimal = Decimal("0"),
        outstanding_deposits: Decimal = Decimal("0"),
        outstanding_payments: Decimal = Decimal("0"),
        reconciliation_difference: Decimal = Decimal("0"),
        prior_outstanding_deposits: Decimal = Decimal("0"),
        prior_outstanding_payments: Decimal = Decimal("0"),
        notes: str | None = None,
        prepared_by: uuid.UUID | None = None,
        prepared_at: datetime | None = None,
        approved_by: uuid.UUID | None = None,
        approved_at: datetime | None = None,
        reviewed_by: uuid.UUID | None = None,
        reviewed_at: datetime | None = None,
        review_notes: str | None = None,
    ):
        self.reconciliation_id = reconciliation_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.bank_account_id = bank_account_id or uuid.uuid4()
        self.reconciliation_date = reconciliation_date or date.today()
        self.period_start = period_start or date.today().replace(day=1)
        self.period_end = period_end or date.today()
        self.statement_opening_balance = statement_opening_balance
        self.statement_closing_balance = statement_closing_balance
        self.gl_opening_balance = gl_opening_balance
        self.gl_closing_balance = gl_closing_balance
        self.currency_code = currency_code
        self.status = status
        self.total_matched = total_matched
        self.total_adjustments = total_adjustments
        self.outstanding_deposits = outstanding_deposits
        self.outstanding_payments = outstanding_payments
        self.reconciliation_difference = reconciliation_difference
        self.prior_outstanding_deposits = prior_outstanding_deposits
        self.prior_outstanding_payments = prior_outstanding_payments
        self.notes = notes
        self.prepared_by = prepared_by
        self.prepared_at = prepared_at
        self.approved_by = approved_by
        self.approved_at = approved_at
        self.reviewed_by = reviewed_by
        self.reviewed_at = reviewed_at
        self.review_notes = review_notes
        self.lines = []
        self.bank_account = None  # Reference to bank account

    def calculate_difference(self):
        """Calculate reconciliation difference."""
        self.reconciliation_difference = (
            self.adjusted_bank_balance - self.adjusted_book_balance
        )

    @property
    def adjusted_book_balance(self) -> Decimal:
        return self.gl_closing_balance + self.total_adjustments

    @property
    def adjusted_bank_balance(self) -> Decimal:
        return (
            self.statement_closing_balance
            + self.outstanding_deposits
            - self.outstanding_payments
        )

    @property
    def is_reconciled(self) -> bool:
        return self.reconciliation_difference == Decimal("0")


class MockBankReconciliationLine:
    """Mock BankReconciliationLine model."""

    def __init__(
        self,
        line_id: uuid.UUID = None,
        reconciliation_id: uuid.UUID = None,
        match_type: ReconciliationMatchType = ReconciliationMatchType.manual,
        statement_line_id: uuid.UUID | None = None,
        journal_line_id: uuid.UUID | None = None,
        transaction_date: date = None,
        description: str | None = None,
        reference: str | None = None,
        statement_amount: Decimal | None = None,
        gl_amount: Decimal | None = None,
        difference: Decimal = Decimal("0"),
        is_cleared: bool = False,
        cleared_at: datetime | None = None,
        is_adjustment: bool = False,
        adjustment_type: str | None = None,
        adjustment_account_id: uuid.UUID | None = None,
        is_outstanding: bool = False,
        outstanding_type: str | None = None,
        notes: str | None = None,
        match_confidence: Decimal | None = None,
        created_by: uuid.UUID | None = None,
    ):
        self.line_id = line_id or uuid.uuid4()
        self.reconciliation_id = reconciliation_id or uuid.uuid4()
        self.match_type = match_type
        self.statement_line_id = statement_line_id
        self.journal_line_id = journal_line_id
        self.transaction_date = transaction_date or date.today()
        self.description = description
        self.reference = reference
        self.statement_amount = statement_amount
        self.gl_amount = gl_amount
        self.difference = difference
        self.is_cleared = is_cleared
        self.cleared_at = cleared_at
        self.is_adjustment = is_adjustment
        self.adjustment_type = adjustment_type
        self.adjustment_account_id = adjustment_account_id
        self.is_outstanding = is_outstanding
        self.outstanding_type = outstanding_type
        self.notes = notes
        self.match_confidence = match_confidence
        self.created_by = created_by


class MockJournalEntry:
    """Mock JournalEntry model."""

    def __init__(
        self,
        entry_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        entry_number: str = "JE-0001",
        entry_date: date = None,
        status: str = "posted",
        description: str | None = None,
    ):
        self.entry_id = entry_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.entry_number = entry_number
        self.entry_date = entry_date or date.today()
        self.status = status
        self.description = description


class MockJournalEntryLine:
    """Mock JournalEntryLine model."""

    def __init__(
        self,
        line_id: uuid.UUID = None,
        entry_id: uuid.UUID = None,
        account_id: uuid.UUID = None,
        debit_amount: Decimal | None = None,
        credit_amount: Decimal | None = None,
        description: str | None = None,
    ):
        self.line_id = line_id or uuid.uuid4()
        self.entry_id = entry_id or uuid.uuid4()
        self.account_id = account_id or uuid.uuid4()
        self.debit_amount = debit_amount
        self.credit_amount = credit_amount
        self.description = description
        self.entry = None  # Reference to parent entry


# ============ Fixtures ============


@pytest.fixture
def organization_id() -> uuid.UUID:
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
    session.filter = MagicMock(return_value=session)
    session.first = MagicMock(return_value=None)
    session.all = MagicMock(return_value=[])
    session.add = MagicMock()
    session.commit = MagicMock()
    session.flush = MagicMock()
    session.refresh = MagicMock()
    session.delete = MagicMock()
    session.get = MagicMock(return_value=None)
    session.execute = MagicMock()
    session.scalar = MagicMock(return_value=None)
    return session


@pytest.fixture
def mock_gl_account(organization_id) -> MockGLAccount:
    """Create a mock GL account."""
    return MockGLAccount(organization_id=organization_id)


@pytest.fixture
def mock_bank_account(organization_id, mock_gl_account) -> MockBankAccount:
    """Create a mock bank account."""
    return MockBankAccount(
        organization_id=organization_id,
        gl_account_id=mock_gl_account.account_id,
    )


@pytest.fixture
def mock_bank_statement(organization_id, mock_bank_account) -> MockBankStatement:
    """Create a mock bank statement."""
    return MockBankStatement(
        organization_id=organization_id,
        bank_account_id=mock_bank_account.bank_account_id,
    )


@pytest.fixture
def mock_bank_reconciliation(
    organization_id, mock_bank_account
) -> MockBankReconciliation:
    """Create a mock bank reconciliation."""
    recon = MockBankReconciliation(
        organization_id=organization_id,
        bank_account_id=mock_bank_account.bank_account_id,
    )
    recon.bank_account = mock_bank_account
    return recon


@pytest.fixture
def mock_statement_line(mock_bank_statement) -> MockBankStatementLine:
    """Create a mock statement line."""
    line = MockBankStatementLine(
        statement_id=mock_bank_statement.statement_id,
    )
    line.statement = mock_bank_statement
    return line


@pytest.fixture
def mock_journal_entry(organization_id) -> MockJournalEntry:
    """Create a mock journal entry."""
    return MockJournalEntry(organization_id=organization_id)


@pytest.fixture
def mock_journal_line(mock_journal_entry, mock_gl_account) -> MockJournalEntryLine:
    """Create a mock journal entry line."""
    line = MockJournalEntryLine(
        entry_id=mock_journal_entry.entry_id,
        account_id=mock_gl_account.account_id,
        debit_amount=Decimal("100.00"),
    )
    line.entry = mock_journal_entry
    return line
