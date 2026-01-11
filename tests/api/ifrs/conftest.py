"""
Fixtures for IFRS API Integration Tests.

These tests mock the service layer to test API routing and serialization
since IFRS models use PostgreSQL-specific types not compatible with SQLite.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ============ Mock Models ============


class MockBankAccount:
    """Mock BankAccount model."""

    def __init__(
        self,
        bank_account_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        bank_name: str = "Test Bank",
        account_number: str = "1234567890",
        account_name: str = "Operating Account",
        gl_account_id: uuid.UUID = None,
        currency_code: str = "USD",
        account_type: str = "checking",
        bank_code: Optional[str] = None,
        branch_code: Optional[str] = None,
        branch_name: Optional[str] = None,
        iban: Optional[str] = None,
        contact_name: Optional[str] = None,
        contact_phone: Optional[str] = None,
        contact_email: Optional[str] = None,
        notes: Optional[str] = None,
        status: str = "active",
        is_primary: bool = False,
        allow_overdraft: bool = False,
        overdraft_limit: Optional[Decimal] = None,
        normal_balance: str = "DEBIT",
        last_statement_balance: Optional[Decimal] = None,
        last_statement_date: Optional[datetime] = None,
        last_reconciled_date: Optional[datetime] = None,
        last_reconciled_balance: Optional[Decimal] = None,
        created_at: datetime = None,
        updated_at: Optional[datetime] = None,
        **kwargs
    ):
        self.bank_account_id = bank_account_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.bank_name = bank_name
        self.account_number = account_number
        self.account_name = account_name
        self.gl_account_id = gl_account_id or uuid.uuid4()
        self.currency_code = currency_code
        self.account_type = account_type
        self.bank_code = bank_code
        self.branch_code = branch_code
        self.branch_name = branch_name
        self.iban = iban
        self.contact_name = contact_name
        self.contact_phone = contact_phone
        self.contact_email = contact_email
        self.notes = notes
        self.status = status
        self.is_primary = is_primary
        self.allow_overdraft = allow_overdraft
        self.overdraft_limit = overdraft_limit
        self.normal_balance = normal_balance
        self.last_statement_balance = last_statement_balance
        self.last_statement_date = last_statement_date
        self.last_reconciled_date = last_reconciled_date
        self.last_reconciled_balance = last_reconciled_balance
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at
        for k, v in kwargs.items():
            setattr(self, k, v)


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
        opening_balance: Decimal = Decimal("1000"),
        closing_balance: Decimal = Decimal("1500"),
        total_credits: Decimal = Decimal("500"),
        total_debits: Decimal = Decimal("0"),
        currency_code: str = "USD",
        status: str = "imported",
        import_source: Optional[str] = None,
        import_filename: Optional[str] = None,
        imported_at: datetime = None,
        total_lines: int = 0,
        matched_lines: int = 0,
        unmatched_lines: int = 0,
        created_at: datetime = None,
        **kwargs
    ):
        self.statement_id = statement_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.bank_account_id = bank_account_id or uuid.uuid4()
        self.statement_number = statement_number
        self.statement_date = statement_date or date.today()
        self.period_start = period_start or date.today()
        self.period_end = period_end or date.today()
        self.opening_balance = opening_balance
        self.closing_balance = closing_balance
        self.total_credits = total_credits
        self.total_debits = total_debits
        self.currency_code = currency_code
        self.status = status
        self.import_source = import_source
        self.import_filename = import_filename
        self.imported_at = imported_at or datetime.now(timezone.utc)
        self.total_lines = total_lines
        self.matched_lines = matched_lines
        self.unmatched_lines = unmatched_lines
        self.created_at = created_at or datetime.now(timezone.utc)
        for k, v in kwargs.items():
            setattr(self, k, v)


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
        statement_opening_balance: Decimal = Decimal("1000"),
        statement_closing_balance: Decimal = Decimal("1500"),
        gl_opening_balance: Decimal = Decimal("1000"),
        gl_closing_balance: Decimal = Decimal("1450"),
        total_matched: Decimal = Decimal("0"),
        total_unmatched_statement: Decimal = Decimal("0"),
        total_unmatched_gl: Decimal = Decimal("0"),
        total_adjustments: Decimal = Decimal("0"),
        reconciliation_difference: Decimal = Decimal("50"),
        prior_outstanding_deposits: Decimal = Decimal("0"),
        prior_outstanding_payments: Decimal = Decimal("0"),
        outstanding_deposits: Decimal = Decimal("0"),
        outstanding_payments: Decimal = Decimal("0"),
        currency_code: str = "USD",
        status: str = "draft",
        prepared_by: Optional[uuid.UUID] = None,
        prepared_at: Optional[datetime] = None,
        reviewed_by: Optional[uuid.UUID] = None,
        reviewed_at: Optional[datetime] = None,
        approved_by: Optional[uuid.UUID] = None,
        approved_at: Optional[datetime] = None,
        notes: Optional[str] = None,
        review_notes: Optional[str] = None,
        created_at: datetime = None,
        updated_at: Optional[datetime] = None,
        **kwargs
    ):
        self.reconciliation_id = reconciliation_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.bank_account_id = bank_account_id or uuid.uuid4()
        self.reconciliation_date = reconciliation_date or date.today()
        self.period_start = period_start or date.today()
        self.period_end = period_end or date.today()
        self.statement_opening_balance = statement_opening_balance
        self.statement_closing_balance = statement_closing_balance
        self.gl_opening_balance = gl_opening_balance
        self.gl_closing_balance = gl_closing_balance
        self.total_matched = total_matched
        self.total_unmatched_statement = total_unmatched_statement
        self.total_unmatched_gl = total_unmatched_gl
        self.total_adjustments = total_adjustments
        self.reconciliation_difference = reconciliation_difference
        self.prior_outstanding_deposits = prior_outstanding_deposits
        self.prior_outstanding_payments = prior_outstanding_payments
        self.outstanding_deposits = outstanding_deposits
        self.outstanding_payments = outstanding_payments
        self.currency_code = currency_code
        self.status = status
        self.prepared_by = prepared_by
        self.prepared_at = prepared_at
        self.reviewed_by = reviewed_by
        self.reviewed_at = reviewed_at
        self.approved_by = approved_by
        self.approved_at = approved_at
        self.notes = notes
        self.review_notes = review_notes
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockAccount:
    """Mock GL Account model."""

    def __init__(
        self,
        account_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        account_code: str = "1000",
        account_name: str = "Cash",
        account_type: str = "POSTING",
        normal_balance: str = "DEBIT",
        description: Optional[str] = None,
        parent_account_id: Optional[uuid.UUID] = None,
        category_id: uuid.UUID = None,
        is_control_account: bool = False,
        is_reconcilable: bool = False,
        is_active: bool = True,
        created_at: datetime = None,
        updated_at: Optional[datetime] = None,
        **kwargs
    ):
        self.account_id = account_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.account_code = account_code
        self.account_name = account_name
        self.account_type = account_type
        self.normal_balance = normal_balance
        self.description = description
        self.parent_account_id = parent_account_id
        self.category_id = category_id or uuid.uuid4()
        self.is_control_account = is_control_account
        self.is_reconcilable = is_reconcilable
        self.is_active = is_active
        self.created_at = created_at or datetime.now(timezone.utc)
        self.updated_at = updated_at
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockFiscalPeriod:
    """Mock FiscalPeriod model."""

    def __init__(
        self,
        fiscal_period_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        fiscal_year_id: uuid.UUID = None,
        period_number: int = 1,
        period_name: str = "January 2024",
        period_type: str = "MONTHLY",
        start_date: date = None,
        end_date: date = None,
        status: str = "OPEN",
        fiscal_year: int = 2024,
        is_adjustment_period: bool = False,
        created_at: datetime = None,
        **kwargs
    ):
        self.fiscal_period_id = fiscal_period_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.fiscal_year_id = fiscal_year_id or uuid.uuid4()
        self.period_number = period_number
        self.period_name = period_name
        self.period_type = period_type
        self.start_date = start_date or date(2024, 1, 1)
        self.end_date = end_date or date(2024, 1, 31)
        self.status = status
        self.fiscal_year = fiscal_year
        self.is_adjustment_period = is_adjustment_period
        self.created_at = created_at or datetime.now(timezone.utc)
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockJournalEntry:
    """Mock JournalEntry model.

    Note: Field names match the JournalEntryRead schema (entry_id, entry_number)
    rather than the database model (journal_entry_id, journal_number).
    """

    def __init__(
        self,
        entry_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        entry_number: str = "JE-0001",
        fiscal_period_id: uuid.UUID = None,
        journal_date: date = None,
        description: str = "Test journal",
        source_module: str = "GL",
        status: str = "DRAFT",
        total_debit: Decimal = Decimal("1000"),
        total_credit: Decimal = Decimal("1000"),
        created_at: datetime = None,
        created_by_user_id: uuid.UUID = None,
        lines: list = None,
        # Aliases for backward compatibility with tests
        journal_entry_id: uuid.UUID = None,
        journal_number: str = None,
        **kwargs
    ):
        # Use entry_id or fall back to journal_entry_id
        self.entry_id = entry_id or journal_entry_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        # Use entry_number or fall back to journal_number
        self.entry_number = entry_number if journal_number is None else journal_number
        self.fiscal_period_id = fiscal_period_id or uuid.uuid4()
        self.journal_date = journal_date or date.today()
        self.description = description
        self.source_module = source_module
        self.status = status
        self.total_debit = total_debit
        self.total_credit = total_credit
        self.created_at = created_at or datetime.now(timezone.utc)
        self.created_by_user_id = created_by_user_id or uuid.uuid4()
        self.lines = lines if lines is not None else []
        # Also provide the old names for tests that use them
        self.journal_entry_id = self.entry_id
        self.journal_number = self.entry_number
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockInventoryItem:
    """Mock InventoryItem model."""

    def __init__(
        self,
        item_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        item_code: str = "ITEM-001",
        item_name: str = "Test Item",
        base_uom: str = "EACH",
        costing_method: str = "WEIGHTED_AVERAGE",
        standard_cost: Optional[Decimal] = None,
        average_cost: Optional[Decimal] = Decimal("10.00"),
        last_purchase_cost: Optional[Decimal] = None,
        list_price: Optional[Decimal] = None,
        reorder_point: Optional[Decimal] = None,
        reorder_quantity: Optional[Decimal] = None,
        minimum_stock: Optional[Decimal] = None,
        maximum_stock: Optional[Decimal] = None,
        track_inventory: bool = True,
        track_lots: bool = False,
        track_serial_numbers: bool = False,
        is_active: bool = True,
        is_purchaseable: bool = True,
        is_saleable: bool = True,
        **kwargs
    ):
        self.item_id = item_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.item_code = item_code
        self.item_name = item_name
        self.base_uom = base_uom
        self.costing_method = costing_method
        self.standard_cost = standard_cost
        self.average_cost = average_cost
        self.last_purchase_cost = last_purchase_cost
        self.list_price = list_price
        self.reorder_point = reorder_point
        self.reorder_quantity = reorder_quantity
        self.minimum_stock = minimum_stock
        self.maximum_stock = maximum_stock
        self.track_inventory = track_inventory
        self.track_lots = track_lots
        self.track_serial_numbers = track_serial_numbers
        self.is_active = is_active
        self.is_purchaseable = is_purchaseable
        self.is_saleable = is_saleable
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockInventoryTransaction:
    """Mock InventoryTransaction model."""

    def __init__(
        self,
        transaction_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        item_id: uuid.UUID = None,
        warehouse_id: uuid.UUID = None,
        transaction_type: str = "RECEIPT",
        transaction_date: date = None,
        quantity: Decimal = Decimal("100"),
        unit_cost: Decimal = Decimal("10.00"),
        total_cost: Decimal = Decimal("1000.00"),
        quantity_before: Decimal = Decimal("0"),
        quantity_after: Decimal = Decimal("100"),
        reference: Optional[str] = None,
        **kwargs
    ):
        self.transaction_id = transaction_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.item_id = item_id or uuid.uuid4()
        self.warehouse_id = warehouse_id or uuid.uuid4()
        self.transaction_type = transaction_type
        self.transaction_date = transaction_date or date.today()
        self.quantity = quantity
        self.unit_cost = unit_cost
        self.total_cost = total_cost
        self.quantity_before = quantity_before
        self.quantity_after = quantity_after
        self.reference = reference
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockLot:
    """Mock InventoryLot model."""

    def __init__(
        self,
        lot_id: uuid.UUID = None,
        item_id: uuid.UUID = None,
        lot_number: str = "LOT-001",
        received_date: date = None,
        expiry_date: Optional[date] = None,
        initial_quantity: Decimal = Decimal("100"),
        quantity_on_hand: Decimal = Decimal("100"),
        quantity_available: Decimal = Decimal("100"),
        unit_cost: Decimal = Decimal("10.00"),
        is_quarantined: bool = False,
        is_active: bool = True,
        **kwargs
    ):
        self.lot_id = lot_id or uuid.uuid4()
        self.item_id = item_id or uuid.uuid4()
        self.lot_number = lot_number
        self.received_date = received_date or date.today()
        self.expiry_date = expiry_date
        self.initial_quantity = initial_quantity
        self.quantity_on_hand = quantity_on_hand
        self.quantity_available = quantity_available
        self.unit_cost = unit_cost
        self.is_quarantined = is_quarantined
        self.is_active = is_active
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockStatementLine:
    """Mock BankStatementLine model."""

    def __init__(
        self,
        line_id: uuid.UUID = None,
        statement_id: uuid.UUID = None,
        line_number: int = 1,
        transaction_id: Optional[str] = None,
        transaction_date: date = None,
        value_date: Optional[date] = None,
        transaction_type: str = "credit",
        amount: Decimal = Decimal("100.00"),
        running_balance: Optional[Decimal] = None,
        description: Optional[str] = "Test transaction",
        reference: Optional[str] = None,
        payee_payer: Optional[str] = None,
        bank_reference: Optional[str] = None,
        check_number: Optional[str] = None,
        bank_category: Optional[str] = None,
        is_matched: bool = False,
        matched_at: Optional[datetime] = None,
        matched_journal_line_id: Optional[uuid.UUID] = None,
        created_at: datetime = None,
        **kwargs
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
        self.is_matched = is_matched
        self.matched_at = matched_at
        self.matched_journal_line_id = matched_journal_line_id
        self.created_at = created_at or datetime.now(timezone.utc)
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockReconciliationLine:
    """Mock ReconciliationLine model."""

    def __init__(
        self,
        line_id: uuid.UUID = None,
        reconciliation_id: uuid.UUID = None,
        match_type: str = "manual",
        statement_line_id: Optional[uuid.UUID] = None,
        journal_line_id: Optional[uuid.UUID] = None,
        transaction_date: date = None,
        description: Optional[str] = "Matched transaction",
        reference: Optional[str] = None,
        statement_amount: Optional[Decimal] = Decimal("100.00"),
        gl_amount: Optional[Decimal] = Decimal("100.00"),
        difference: Optional[Decimal] = Decimal("0"),
        is_adjustment: bool = False,
        adjustment_type: Optional[str] = None,
        is_outstanding: bool = False,
        outstanding_type: Optional[str] = None,
        match_confidence: Optional[Decimal] = None,
        is_cleared: bool = False,
        notes: Optional[str] = None,
        created_at: datetime = None,
        **kwargs
    ):
        self.line_id = line_id or uuid.uuid4()
        self.reconciliation_id = reconciliation_id or uuid.uuid4()
        self.match_type = match_type
        self.statement_line_id = statement_line_id or uuid.uuid4()
        self.journal_line_id = journal_line_id or uuid.uuid4()
        self.transaction_date = transaction_date or date.today()
        self.description = description
        self.reference = reference
        self.statement_amount = statement_amount
        self.gl_amount = gl_amount
        self.difference = difference
        self.is_adjustment = is_adjustment
        self.adjustment_type = adjustment_type
        self.is_outstanding = is_outstanding
        self.outstanding_type = outstanding_type
        self.match_confidence = match_confidence
        self.is_cleared = is_cleared
        self.notes = notes
        self.created_at = created_at or datetime.now(timezone.utc)
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockStatementImportResult:
    """Mock StatementImportResult."""

    def __init__(
        self,
        statement: "MockBankStatement" = None,
        lines_imported: int = 10,
        lines_skipped: int = 0,
        errors: list = None,
        warnings: list = None,
        **kwargs
    ):
        self.statement = statement or MockBankStatement()
        self.lines_imported = lines_imported
        self.lines_skipped = lines_skipped
        self.errors = errors or []
        self.warnings = warnings or []
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockAccountBalance:
    """Mock AccountBalance model."""

    def __init__(
        self,
        account_id: uuid.UUID = None,
        account_code: str = "1000",
        account_name: str = "Cash",
        fiscal_period_id: uuid.UUID = None,
        opening_balance: Decimal = Decimal("0"),
        period_debit: Decimal = Decimal("1000"),
        period_credit: Decimal = Decimal("500"),
        closing_balance: Decimal = Decimal("500"),
        currency_code: str = "USD",
        **kwargs
    ):
        self.account_id = account_id or uuid.uuid4()
        self.account_code = account_code
        self.account_name = account_name
        self.fiscal_period_id = fiscal_period_id or uuid.uuid4()
        self.opening_balance = opening_balance
        self.period_debit = period_debit
        self.period_credit = period_credit
        self.closing_balance = closing_balance
        self.currency_code = currency_code
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockTrialBalanceLine:
    """Mock TrialBalanceLine."""

    def __init__(
        self,
        account_id: uuid.UUID = None,
        account_code: str = "1000",
        account_name: str = "Cash",
        account_type: str = "ASSET",
        debit_balance: Decimal = Decimal("1000"),
        credit_balance: Decimal = Decimal("0"),
        **kwargs
    ):
        self.account_id = account_id or uuid.uuid4()
        self.account_code = account_code
        self.account_name = account_name
        self.account_type = account_type
        self.debit_balance = debit_balance
        self.credit_balance = credit_balance
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockTrialBalance:
    """Mock TrialBalance."""

    def __init__(
        self,
        fiscal_period_id: uuid.UUID = None,
        period_name: str = "January 2024",
        as_of_date: date = None,
        lines: list = None,
        total_debit: Decimal = Decimal("10000"),
        total_credit: Decimal = Decimal("10000"),
        is_balanced: bool = True,
        **kwargs
    ):
        self.fiscal_period_id = fiscal_period_id or uuid.uuid4()
        self.period_name = period_name
        self.as_of_date = as_of_date or date.today()
        self.lines = lines or []
        self.total_debit = total_debit
        self.total_credit = total_credit
        self.is_balanced = is_balanced
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockPostingResult:
    """Mock PostingResult."""

    def __init__(
        self,
        success: bool = True,
        entry_id: uuid.UUID = None,
        entry_number: str = "JE-0001",
        message: str = "Posted successfully",
        errors: list = None,
        **kwargs
    ):
        self.success = success
        self.entry_id = entry_id or uuid.uuid4()
        self.entry_number = entry_number
        self.message = message
        self.errors = errors or []
        for k, v in kwargs.items():
            setattr(self, k, v)


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
def mock_auth_dict(org_id, user_id):
    """Create a mock auth dictionary."""
    return {
        "person_id": str(user_id),
        "organization_id": str(org_id),
        "roles": ["admin"],
        "scopes": ["*"],
    }
