"""
GL Schemas.

Pydantic schemas for General Ledger APIs.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Chart of Accounts
# =============================================================================


class AccountBase(BaseModel):
    """Base account schema."""

    account_code: str = Field(max_length=50)
    account_name: str = Field(max_length=200)
    account_type: str = Field(max_length=30)
    normal_balance: str = Field(max_length=10)
    description: str | None = None
    parent_account_id: UUID | None = None
    is_control_account: bool = False
    is_reconcilable: bool = False
    is_active: bool = True


class AccountCreate(AccountBase):
    """Create account request."""

    pass


class AccountUpdate(BaseModel):
    """Update account request."""

    account_name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    search_terms: str | None = None
    is_active: bool | None = None
    is_posting_allowed: bool | None = None
    is_budgetable: bool | None = None
    is_reconciliation_required: bool | None = None
    is_multi_currency: bool | None = None
    default_currency_code: str | None = Field(default=None, max_length=3)
    subledger_type: str | None = Field(default=None, max_length=20)
    is_cash_equivalent: bool | None = None
    is_financial_instrument: bool | None = None


class AccountRead(BaseModel):
    """Account response."""

    model_config = ConfigDict(from_attributes=True)

    account_id: UUID
    organization_id: UUID
    category_id: UUID
    account_code: str
    account_name: str
    description: str | None = None
    search_terms: str | None = None
    account_type: str
    normal_balance: str
    is_multi_currency: bool = False
    default_currency_code: str | None = None
    is_active: bool = True
    is_posting_allowed: bool = True
    is_budgetable: bool = True
    is_reconciliation_required: bool = False
    subledger_type: str | None = None
    is_cash_equivalent: bool = False
    is_financial_instrument: bool = False
    created_by_user_id: UUID | None = None
    created_at: datetime
    updated_by_user_id: UUID | None = None
    updated_at: datetime | None = None


# =============================================================================
# Fiscal Period
# =============================================================================


class FiscalPeriodBase(BaseModel):
    """Base fiscal period schema."""

    period_name: str = Field(max_length=50)
    period_number: int
    start_date: date
    end_date: date


class FiscalPeriodCreate(FiscalPeriodBase):
    """Create fiscal period request."""

    fiscal_year_id: UUID


class FiscalPeriodRead(BaseModel):
    """Fiscal period response."""

    model_config = ConfigDict(from_attributes=True)

    fiscal_period_id: UUID
    organization_id: UUID
    fiscal_year_id: UUID
    period_name: str
    period_number: int
    start_date: date
    end_date: date
    status: str
    is_adjustment_period: bool
    created_at: datetime


# =============================================================================
# Journal Entry
# =============================================================================


class JournalLineCreate(BaseModel):
    """Journal line for entry creation."""

    account_id: UUID
    debit_amount: Decimal = Field(default=Decimal("0"), decimal_places=2)
    credit_amount: Decimal = Field(default=Decimal("0"), decimal_places=2)
    currency_code: str = Field(max_length=3)
    description: str | None = None
    cost_center_id: UUID | None = None
    project_id: UUID | None = None


class JournalEntryCreate(BaseModel):
    """Create journal entry request."""

    fiscal_period_id: UUID
    journal_date: date
    description: str = Field(max_length=500)
    source_module: str = Field(max_length=30)
    source_document_type: str | None = None
    source_document_id: UUID | None = None
    reference_number: str | None = None
    lines: list[JournalLineCreate]


class JournalLineRead(BaseModel):
    """Journal line response."""

    model_config = ConfigDict(from_attributes=True)

    line_id: UUID
    account_id: UUID
    account_code: str | None = None
    account_name: str | None = None
    debit_amount: Decimal
    credit_amount: Decimal
    currency_code: str
    description: str | None = None


class JournalEntryRead(BaseModel):
    """Journal entry response."""

    model_config = ConfigDict(from_attributes=True)

    journal_entry_id: UUID
    organization_id: UUID
    fiscal_period_id: UUID
    journal_number: str
    journal_type: str
    entry_date: date
    posting_date: date
    description: str
    status: str
    total_debit: Decimal
    total_credit: Decimal
    created_at: datetime
    created_by_user_id: UUID
    lines: list[JournalLineRead] = []


# =============================================================================
# Account Balance
# =============================================================================


class AccountBalanceRead(BaseModel):
    """Account balance response."""

    model_config = ConfigDict(from_attributes=True)

    account_id: UUID
    account_code: str
    account_name: str
    fiscal_period_id: UUID
    opening_balance: Decimal
    period_debit: Decimal
    period_credit: Decimal
    closing_balance: Decimal
    currency_code: str


class TrialBalanceLineRead(BaseModel):
    """Trial balance line."""

    account_id: UUID
    account_code: str
    account_name: str
    account_type: str
    debit_balance: Decimal
    credit_balance: Decimal


class TrialBalanceRead(BaseModel):
    """Trial balance response."""

    fiscal_period_id: UUID
    period_name: str
    as_of_date: date
    lines: list[TrialBalanceLineRead]
    total_debit: Decimal
    total_credit: Decimal
    is_balanced: bool


__all__ = [
    "AccountCreate",
    "AccountUpdate",
    "AccountRead",
    "FiscalPeriodCreate",
    "FiscalPeriodRead",
    "JournalLineCreate",
    "JournalEntryCreate",
    "JournalLineRead",
    "JournalEntryRead",
    "AccountBalanceRead",
    "TrialBalanceLineRead",
    "TrialBalanceRead",
]
