"""
Banking Schemas.

Pydantic schemas for Banking APIs.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import settings
from app.models.finance.banking.bank_account import BankAccountStatus, BankAccountType
from app.models.finance.banking.bank_reconciliation import (
    ReconciliationMatchType,
    ReconciliationStatus,
)
from app.models.finance.banking.bank_statement import (
    BankStatementStatus,
    StatementLineType,
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
    currency_code: str = Field(
        default=settings.default_functional_currency_code, max_length=3
    )
    account_type: BankAccountType = BankAccountType.checking
    bank_code: str | None = Field(default=None, max_length=20)
    branch_code: str | None = Field(default=None, max_length=20)
    branch_name: str | None = Field(default=None, max_length=200)
    iban: str | None = Field(default=None, max_length=50)
    contact_name: str | None = Field(default=None, max_length=200)
    contact_phone: str | None = Field(default=None, max_length=50)
    contact_email: str | None = Field(default=None, max_length=200)
    notes: str | None = None
    is_primary: bool = False
    allow_overdraft: bool = False
    overdraft_limit: Decimal | None = None


class BankAccountCreate(BankAccountBase):
    """Create bank account request."""

    pass


class BankAccountUpdate(BaseModel):
    """Update bank account request."""

    bank_name: str | None = Field(default=None, max_length=200)
    account_name: str | None = Field(default=None, max_length=200)
    gl_account_id: UUID | None = None
    account_type: BankAccountType | None = None
    bank_code: str | None = Field(default=None, max_length=20)
    branch_code: str | None = Field(default=None, max_length=20)
    branch_name: str | None = Field(default=None, max_length=200)
    iban: str | None = Field(default=None, max_length=50)
    contact_name: str | None = Field(default=None, max_length=200)
    contact_phone: str | None = Field(default=None, max_length=50)
    contact_email: str | None = Field(default=None, max_length=200)
    notes: str | None = None
    is_primary: bool | None = None
    allow_overdraft: bool | None = None
    overdraft_limit: Decimal | None = None


class BankAccountRead(BankAccountBase):
    """Bank account response."""

    model_config = ConfigDict(from_attributes=True)

    bank_account_id: UUID
    organization_id: UUID
    status: BankAccountStatus
    last_statement_balance: Decimal | None = None
    last_statement_date: datetime | None = None
    last_reconciled_date: datetime | None = None
    last_reconciled_balance: Decimal | None = None
    created_at: datetime
    updated_at: datetime | None = None


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
    transaction_type: StatementLineType | None = None
    amount: Decimal | None = Field(default=None, ge=0)
    debit: Decimal | None = Field(default=None, ge=0)
    credit: Decimal | None = Field(default=None, ge=0)
    description: str | None = Field(default=None, max_length=500)
    reference: str | None = Field(default=None, max_length=100)
    payee_payer: str | None = Field(default=None, max_length=200)
    bank_reference: str | None = Field(default=None, max_length=100)
    check_number: str | None = Field(default=None, max_length=20)
    bank_category: str | None = Field(default=None, max_length=100)
    bank_code: str | None = Field(default=None, max_length=20)
    value_date: date | None = None
    running_balance: Decimal | None = None
    transaction_id: str | None = Field(default=None, max_length=100)
    raw_data: dict | None = None

    @model_validator(mode="after")
    def _validate_amount_fields(self) -> "StatementLineCreate":
        has_type = self.transaction_type is not None
        has_amount = self.amount is not None
        debit_value = self.debit or Decimal("0")
        credit_value = self.credit or Decimal("0")
        has_debit = debit_value > 0
        has_credit = credit_value > 0
        has_debit_credit = self.debit is not None or self.credit is not None

        if has_type:
            if not has_amount:
                raise ValueError("amount is required when transaction_type is provided")
            if has_debit or has_credit:
                raise ValueError(
                    "Provide either transaction_type+amount or debit/credit, not both"
                )
            return self

        if has_amount:
            raise ValueError(
                "transaction_type is required when amount is provided without debit/credit"
            )

        if not has_debit_credit or not (has_debit or has_credit):
            raise ValueError("Provide either transaction_type+amount or debit/credit")
        if has_debit and has_credit:
            raise ValueError("Only one of debit or credit can be greater than 0")

        return self


class BankStatementImport(BaseModel):
    """Import bank statement request."""

    bank_account_id: UUID
    statement_number: str | None = Field(default=None, max_length=50)
    statement_date: date | None = None
    period_start: date
    period_end: date
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    lines: list[StatementLineCreate]
    import_source: str | None = Field(default=None, max_length=50)
    import_filename: str | None = Field(default=None, max_length=255)


class StatementLineRead(BaseModel):
    """Statement line response."""

    model_config = ConfigDict(from_attributes=True)

    line_id: UUID
    statement_id: UUID
    line_number: int
    transaction_id: str | None = None
    transaction_date: date
    value_date: date | None = None
    transaction_type: StatementLineType
    amount: Decimal
    running_balance: Decimal | None = None
    description: str | None = None
    reference: str | None = None
    payee_payer: str | None = None
    bank_reference: str | None = None
    check_number: str | None = None
    bank_category: str | None = None
    is_matched: bool
    matched_at: datetime | None = None
    matched_journal_line_id: UUID | None = None
    created_at: datetime


class BankStatementRead(BaseModel):
    """Bank statement response."""

    model_config = ConfigDict(from_attributes=True)

    statement_id: UUID
    organization_id: UUID
    bank_account_id: UUID
    statement_number: str | None = None
    statement_date: date | None = None
    period_start: date
    period_end: date
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    total_credits: Decimal
    total_debits: Decimal
    currency_code: str
    status: BankStatementStatus
    import_source: str | None = None
    import_filename: str | None = None
    imported_at: datetime
    total_lines: int
    matched_lines: int
    unmatched_lines: int
    created_at: datetime


class BankStatementWithLines(BankStatementRead):
    """Bank statement with lines response."""

    lines: list[StatementLineRead] = []


class StatementImportResult(BaseModel):
    """Statement import result response."""

    statement: BankStatementRead
    lines_imported: int
    lines_skipped: int
    errors: list[str] = []
    warnings: list[str] = []


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
    notes: str | None = None


class ReconciliationMatchCreate(BaseModel):
    """Create match request."""

    statement_line_id: UUID
    journal_line_id: UUID
    match_type: ReconciliationMatchType = ReconciliationMatchType.manual
    notes: str | None = None


class ReconciliationMultiMatchCreate(BaseModel):
    """Create a many-to-one or one-to-many match."""

    statement_line_ids: list[UUID] = Field(min_length=1)
    journal_line_ids: list[UUID] = Field(min_length=1)
    notes: str | None = None
    tolerance: Decimal = Field(default=Decimal("0.01"), ge=0)


class ReconciliationAdjustmentCreate(BaseModel):
    """Create adjustment request."""

    transaction_date: date
    amount: Decimal
    description: str = Field(max_length=500)
    adjustment_type: str = Field(max_length=50)
    adjustment_account_id: UUID | None = None


class ReconciliationOutstandingCreate(BaseModel):
    """Create outstanding item request."""

    transaction_date: date
    amount: Decimal = Field(ge=0)
    description: str = Field(max_length=500)
    outstanding_type: str = Field(pattern="^(deposit|payment)$")
    reference: str | None = Field(default=None, max_length=100)
    journal_line_id: UUID | None = None


class ReconciliationLineRead(BaseModel):
    """Reconciliation line response."""

    model_config = ConfigDict(from_attributes=True)

    line_id: UUID
    reconciliation_id: UUID
    match_type: ReconciliationMatchType
    statement_line_id: UUID | None = None
    journal_line_id: UUID | None = None
    transaction_date: date
    description: str | None = None
    reference: str | None = None
    statement_amount: Decimal | None = None
    gl_amount: Decimal | None = None
    difference: Decimal | None = None
    is_adjustment: bool
    adjustment_type: str | None = None
    is_outstanding: bool
    outstanding_type: str | None = None
    match_confidence: Decimal | None = None
    is_cleared: bool
    notes: str | None = None
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
    prepared_by: UUID | None = None
    prepared_at: datetime | None = None
    reviewed_by: UUID | None = None
    reviewed_at: datetime | None = None
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    notes: str | None = None
    review_notes: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class BankReconciliationWithLines(BankReconciliationRead):
    """Bank reconciliation with lines response."""

    lines: list[ReconciliationLineRead] = []


class AutoMatchRequest(BaseModel):
    """Auto-match request."""

    tolerance: Decimal = Field(default=Decimal("0.01"), ge=0)


class AutoMatchResult(BaseModel):
    """Auto-match result response."""

    matches_found: int
    matches_created: int
    unmatched_statement_lines: int
    unmatched_gl_lines: int
    match_details: list[dict] = []


class ReconciliationApproval(BaseModel):
    """Reconciliation approval request."""

    notes: str | None = None


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
    items: list[ReconciliationLineRead] = []


class ReconciliationReport(BaseModel):
    """Full reconciliation report."""

    reconciliation: BankReconciliationRead
    summary: ReconciliationReportSummary
    matched_items: ReconciliationReportSection
    adjustments: ReconciliationReportSection
    outstanding_deposits: ReconciliationReportSection
    outstanding_payments: ReconciliationReportSection
