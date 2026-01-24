"""
Banking Schemas.

Pydantic schemas for Banking APIs.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.models.finance.banking.bank_account import BankAccountStatus, BankAccountType
from app.models.finance.banking.bank_statement import BankStatementStatus, StatementLineType
from app.models.finance.banking.bank_reconciliation import (
    ReconciliationMatchType,
    ReconciliationStatus,
)


# =============================================================================
# Bank Account
# =============================================================================


class BankAccountBase(BaseModel):
    """Base bank account schema."""

    bank_name: str = Field(max_length=200)
    account_number: str = Field(max_length=50)
    account_name: str = Field(max_length=200)
    gl_account_id: UUID
    currency_code: str = Field(default=settings.default_functional_currency_code, max_length=3)
    account_type: BankAccountType = BankAccountType.checking
    bank_code: Optional[str] = Field(default=None, max_length=20)
    branch_code: Optional[str] = Field(default=None, max_length=20)
    branch_name: Optional[str] = Field(default=None, max_length=200)
    iban: Optional[str] = Field(default=None, max_length=50)
    contact_name: Optional[str] = Field(default=None, max_length=200)
    contact_phone: Optional[str] = Field(default=None, max_length=50)
    contact_email: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = None
    is_primary: bool = False
    allow_overdraft: bool = False
    overdraft_limit: Optional[Decimal] = None


class BankAccountCreate(BankAccountBase):
    """Create bank account request."""

    pass


class BankAccountUpdate(BaseModel):
    """Update bank account request."""

    bank_name: Optional[str] = Field(default=None, max_length=200)
    account_name: Optional[str] = Field(default=None, max_length=200)
    gl_account_id: Optional[UUID] = None
    account_type: Optional[BankAccountType] = None
    bank_code: Optional[str] = Field(default=None, max_length=20)
    branch_code: Optional[str] = Field(default=None, max_length=20)
    branch_name: Optional[str] = Field(default=None, max_length=200)
    iban: Optional[str] = Field(default=None, max_length=50)
    contact_name: Optional[str] = Field(default=None, max_length=200)
    contact_phone: Optional[str] = Field(default=None, max_length=50)
    contact_email: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = None
    is_primary: Optional[bool] = None
    allow_overdraft: Optional[bool] = None
    overdraft_limit: Optional[Decimal] = None


class BankAccountRead(BankAccountBase):
    """Bank account response."""

    model_config = ConfigDict(from_attributes=True)

    bank_account_id: UUID
    organization_id: UUID
    status: BankAccountStatus
    last_statement_balance: Optional[Decimal] = None
    last_statement_date: Optional[datetime] = None
    last_reconciled_date: Optional[datetime] = None
    last_reconciled_balance: Optional[Decimal] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class BankAccountStatusUpdate(BaseModel):
    """Update bank account status request."""

    status: BankAccountStatus


# =============================================================================
# Bank Statement
# =============================================================================


class StatementLineCreate(BaseModel):
    """Bank statement line for import."""

    line_number: int
    transaction_date: date
    transaction_type: StatementLineType
    amount: Decimal = Field(ge=0)
    description: Optional[str] = Field(default=None, max_length=500)
    reference: Optional[str] = Field(default=None, max_length=100)
    payee_payer: Optional[str] = Field(default=None, max_length=200)
    bank_reference: Optional[str] = Field(default=None, max_length=100)
    check_number: Optional[str] = Field(default=None, max_length=20)
    bank_category: Optional[str] = Field(default=None, max_length=100)
    bank_code: Optional[str] = Field(default=None, max_length=20)
    value_date: Optional[date] = None
    running_balance: Optional[Decimal] = None
    transaction_id: Optional[str] = Field(default=None, max_length=100)
    raw_data: Optional[Dict] = None


class BankStatementImport(BaseModel):
    """Import bank statement request."""

    bank_account_id: UUID
    statement_number: str = Field(max_length=50)
    statement_date: date
    period_start: date
    period_end: date
    opening_balance: Decimal
    closing_balance: Decimal
    lines: List[StatementLineCreate]
    import_source: Optional[str] = Field(default=None, max_length=50)
    import_filename: Optional[str] = Field(default=None, max_length=255)


class StatementLineRead(BaseModel):
    """Statement line response."""

    model_config = ConfigDict(from_attributes=True)

    line_id: UUID
    statement_id: UUID
    line_number: int
    transaction_id: Optional[str] = None
    transaction_date: date
    value_date: Optional[date] = None
    transaction_type: StatementLineType
    amount: Decimal
    running_balance: Optional[Decimal] = None
    description: Optional[str] = None
    reference: Optional[str] = None
    payee_payer: Optional[str] = None
    bank_reference: Optional[str] = None
    check_number: Optional[str] = None
    bank_category: Optional[str] = None
    is_matched: bool
    matched_at: Optional[datetime] = None
    matched_journal_line_id: Optional[UUID] = None
    created_at: datetime


class BankStatementRead(BaseModel):
    """Bank statement response."""

    model_config = ConfigDict(from_attributes=True)

    statement_id: UUID
    organization_id: UUID
    bank_account_id: UUID
    statement_number: str
    statement_date: date
    period_start: date
    period_end: date
    opening_balance: Decimal
    closing_balance: Decimal
    total_credits: Decimal
    total_debits: Decimal
    currency_code: str
    status: BankStatementStatus
    import_source: Optional[str] = None
    import_filename: Optional[str] = None
    imported_at: datetime
    total_lines: int
    matched_lines: int
    unmatched_lines: int
    created_at: datetime


class BankStatementWithLines(BankStatementRead):
    """Bank statement with lines response."""

    lines: List[StatementLineRead] = []


class StatementImportResult(BaseModel):
    """Statement import result response."""

    statement: BankStatementRead
    lines_imported: int
    lines_skipped: int
    errors: List[str] = []
    warnings: List[str] = []


class StatementSummary(BaseModel):
    """Statement summary statistics."""

    total_statements: int
    total_lines: int
    matched_lines: int
    unmatched_lines: int
    match_rate: float


# =============================================================================
# Bank Reconciliation
# =============================================================================


class ReconciliationCreate(BaseModel):
    """Create reconciliation request."""

    bank_account_id: UUID
    reconciliation_date: date
    period_start: date
    period_end: date
    statement_opening_balance: Decimal
    statement_closing_balance: Decimal
    notes: Optional[str] = None


class ReconciliationMatchCreate(BaseModel):
    """Create match request."""

    statement_line_id: UUID
    journal_line_id: UUID
    match_type: ReconciliationMatchType = ReconciliationMatchType.manual
    notes: Optional[str] = None


class ReconciliationAdjustmentCreate(BaseModel):
    """Create adjustment request."""

    transaction_date: date
    amount: Decimal
    description: str = Field(max_length=500)
    adjustment_type: str = Field(max_length=50)
    adjustment_account_id: Optional[UUID] = None


class ReconciliationOutstandingCreate(BaseModel):
    """Create outstanding item request."""

    transaction_date: date
    amount: Decimal = Field(ge=0)
    description: str = Field(max_length=500)
    outstanding_type: str = Field(pattern="^(deposit|payment)$")
    reference: Optional[str] = Field(default=None, max_length=100)
    journal_line_id: Optional[UUID] = None


class ReconciliationLineRead(BaseModel):
    """Reconciliation line response."""

    model_config = ConfigDict(from_attributes=True)

    line_id: UUID
    reconciliation_id: UUID
    match_type: ReconciliationMatchType
    statement_line_id: Optional[UUID] = None
    journal_line_id: Optional[UUID] = None
    transaction_date: date
    description: Optional[str] = None
    reference: Optional[str] = None
    statement_amount: Optional[Decimal] = None
    gl_amount: Optional[Decimal] = None
    difference: Optional[Decimal] = None
    is_adjustment: bool
    adjustment_type: Optional[str] = None
    is_outstanding: bool
    outstanding_type: Optional[str] = None
    match_confidence: Optional[Decimal] = None
    is_cleared: bool
    notes: Optional[str] = None
    created_at: datetime


class BankReconciliationRead(BaseModel):
    """Bank reconciliation response."""

    model_config = ConfigDict(from_attributes=True)

    reconciliation_id: UUID
    organization_id: UUID
    bank_account_id: UUID
    reconciliation_date: date
    period_start: date
    period_end: date
    statement_opening_balance: Decimal
    statement_closing_balance: Decimal
    gl_opening_balance: Decimal
    gl_closing_balance: Decimal
    total_matched: Decimal
    total_unmatched_statement: Decimal
    total_unmatched_gl: Decimal
    total_adjustments: Decimal
    reconciliation_difference: Decimal
    prior_outstanding_deposits: Decimal
    prior_outstanding_payments: Decimal
    outstanding_deposits: Decimal
    outstanding_payments: Decimal
    currency_code: str
    status: ReconciliationStatus
    prepared_by: Optional[UUID] = None
    prepared_at: Optional[datetime] = None
    reviewed_by: Optional[UUID] = None
    reviewed_at: Optional[datetime] = None
    approved_by: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    notes: Optional[str] = None
    review_notes: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class BankReconciliationWithLines(BankReconciliationRead):
    """Bank reconciliation with lines response."""

    lines: List[ReconciliationLineRead] = []


class AutoMatchRequest(BaseModel):
    """Auto-match request."""

    tolerance: Decimal = Field(default=Decimal("0.01"), ge=0)


class AutoMatchResult(BaseModel):
    """Auto-match result response."""

    matches_found: int
    matches_created: int
    unmatched_statement_lines: int
    unmatched_gl_lines: int
    match_details: List[Dict] = []


class ReconciliationApproval(BaseModel):
    """Reconciliation approval request."""

    notes: Optional[str] = None


class ReconciliationRejection(BaseModel):
    """Reconciliation rejection request."""

    notes: str = Field(min_length=1)


class ReconciliationReportSummary(BaseModel):
    """Reconciliation report summary."""

    statement_balance: Decimal
    gl_balance: Decimal
    adjusted_book_balance: Decimal
    difference: Decimal
    is_reconciled: bool


class ReconciliationReportSection(BaseModel):
    """Reconciliation report section."""

    count: int
    total: Decimal
    items: List[ReconciliationLineRead] = []


class ReconciliationReport(BaseModel):
    """Full reconciliation report."""

    reconciliation: BankReconciliationRead
    summary: ReconciliationReportSummary
    matched_items: ReconciliationReportSection
    adjustments: ReconciliationReportSection
    outstanding_deposits: ReconciliationReportSection
    outstanding_payments: ReconciliationReportSection
