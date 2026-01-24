"""
Expense Management Pydantic Schemas.

Pydantic schemas for Expense APIs including:
- Expense Category
- Expense Claim
- Cash Advance
- Corporate Card / Card Transaction
"""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.people.exp import (
    ExpenseClaimStatus,
    CashAdvanceStatus,
    CardTransactionStatus,
)


# =============================================================================
# Expense Category Schemas
# =============================================================================


class ExpenseCategoryBase(BaseModel):
    """Base expense category schema."""

    category_code: str = Field(max_length=30)
    category_name: str = Field(max_length=200)
    description: Optional[str] = None
    expense_account_id: Optional[UUID] = None
    max_amount_per_claim: Optional[Decimal] = None
    requires_receipt: bool = True
    is_active: bool = True


class ExpenseCategoryCreate(ExpenseCategoryBase):
    """Create expense category request."""

    pass


class ExpenseCategoryUpdate(BaseModel):
    """Update expense category request."""

    category_code: Optional[str] = Field(default=None, max_length=30)
    category_name: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = None
    expense_account_id: Optional[UUID] = None
    max_amount_per_claim: Optional[Decimal] = None
    requires_receipt: Optional[bool] = None
    is_active: Optional[bool] = None


class ExpenseCategoryRead(ExpenseCategoryBase):
    """Expense category response."""

    model_config = ConfigDict(from_attributes=True)

    category_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None


class ExpenseCategoryListResponse(BaseModel):
    """Paginated expense category list response."""

    items: List[ExpenseCategoryRead]
    total: int
    offset: int
    limit: int


class ExpenseCategoryBrief(BaseModel):
    """Brief expense category info."""

    model_config = ConfigDict(from_attributes=True)

    category_id: UUID
    category_code: str
    category_name: str


# =============================================================================
# Expense Claim Item Schemas
# =============================================================================


class ExpenseClaimItemBase(BaseModel):
    """Base expense claim item schema."""

    expense_date: date
    category_id: UUID
    description: str = Field(max_length=500)
    claimed_amount: Decimal
    expense_account_id: Optional[UUID] = None
    cost_center_id: Optional[UUID] = None
    receipt_url: Optional[str] = Field(default=None, max_length=500)
    receipt_number: Optional[str] = Field(default=None, max_length=50)
    vendor_name: Optional[str] = Field(default=None, max_length=200)
    is_travel_expense: bool = False
    travel_from: Optional[str] = Field(default=None, max_length=200)
    travel_to: Optional[str] = Field(default=None, max_length=200)
    distance_km: Optional[Decimal] = None
    notes: Optional[str] = None


class ExpenseClaimItemCreate(ExpenseClaimItemBase):
    """Create expense claim item request."""

    pass


class ExpenseClaimItemRead(ExpenseClaimItemBase):
    """Expense claim item response."""

    model_config = ConfigDict(from_attributes=True)

    item_id: UUID
    organization_id: UUID
    claim_id: UUID
    approved_amount: Optional[Decimal] = None
    sequence: int

    category: Optional[ExpenseCategoryBrief] = None


# =============================================================================
# Expense Claim Schemas
# =============================================================================


class ExpenseClaimBase(BaseModel):
    """Base expense claim schema."""

    employee_id: UUID
    claim_date: date
    expense_period_start: Optional[date] = None
    expense_period_end: Optional[date] = None
    purpose: str = Field(max_length=500)
    project_id: Optional[UUID] = None
    ticket_id: Optional[UUID] = None
    task_id: Optional[UUID] = None
    currency_code: str = "NGN"
    cost_center_id: Optional[UUID] = None
    recipient_bank_code: Optional[str] = Field(default=None, max_length=20)
    recipient_account_number: Optional[str] = Field(default=None, max_length=20)
    notes: Optional[str] = None


class ExpenseClaimCreate(ExpenseClaimBase):
    """Create expense claim request."""

    items: List[ExpenseClaimItemCreate] = []


class ExpenseClaimUpdate(BaseModel):
    """Update expense claim request."""

    claim_date: Optional[date] = None
    expense_period_start: Optional[date] = None
    expense_period_end: Optional[date] = None
    purpose: Optional[str] = Field(default=None, max_length=500)
    project_id: Optional[UUID] = None
    ticket_id: Optional[UUID] = None
    task_id: Optional[UUID] = None
    cost_center_id: Optional[UUID] = None
    recipient_bank_code: Optional[str] = Field(default=None, max_length=20)
    recipient_account_number: Optional[str] = Field(default=None, max_length=20)
    notes: Optional[str] = None


class EmployeeBrief(BaseModel):
    """Brief employee info."""

    model_config = ConfigDict(from_attributes=True)

    employee_id: UUID
    employee_code: str


class ExpenseClaimRead(ExpenseClaimBase):
    """Expense claim response."""

    model_config = ConfigDict(from_attributes=True)

    claim_id: UUID
    organization_id: UUID
    claim_number: str
    total_claimed_amount: Decimal
    total_approved_amount: Optional[Decimal] = None
    advance_adjusted: Decimal
    cash_advance_id: Optional[UUID] = None
    net_payable_amount: Optional[Decimal] = None
    status: ExpenseClaimStatus
    approver_id: Optional[UUID] = None
    approved_on: Optional[date] = None
    rejection_reason: Optional[str] = None
    supplier_invoice_id: Optional[UUID] = None
    payment_reference: Optional[str] = None
    paid_on: Optional[date] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    employee: Optional[EmployeeBrief] = None
    approver: Optional[EmployeeBrief] = None
    items: List[ExpenseClaimItemRead] = []


class ExpenseClaimListResponse(BaseModel):
    """Paginated expense claim list response."""

    items: List[ExpenseClaimRead]
    total: int
    offset: int
    limit: int


class ExpenseClaimSubmitRequest(BaseModel):
    """Submit expense claim for approval."""

    pass


class ExpenseClaimApprovalRequest(BaseModel):
    """Approve expense claim."""

    approver_id: Optional[UUID] = None
    notes: Optional[str] = None
    approved_amounts: Optional[List["ItemApprovalAmount"]] = None


class ItemApprovalAmount(BaseModel):
    """Approved amount for a claim item."""

    item_id: UUID
    approved_amount: Decimal


class ExpenseClaimCancelRequest(BaseModel):
    """Cancel expense claim request."""

    reason: Optional[str] = None


class LinkAdvanceRequest(BaseModel):
    """Link cash advance to expense claim."""

    advance_id: UUID
    amount_to_adjust: Decimal


# =============================================================================
# Cash Advance Schemas
# =============================================================================


class CashAdvanceBase(BaseModel):
    """Base cash advance schema."""

    employee_id: UUID
    request_date: date
    purpose: str = Field(max_length=500)
    requested_amount: Decimal
    currency_code: str = "NGN"
    expected_settlement_date: Optional[date] = None
    cost_center_id: Optional[UUID] = None
    advance_account_id: Optional[UUID] = None
    notes: Optional[str] = None


class CashAdvanceCreate(CashAdvanceBase):
    """Create cash advance request."""

    pass


class CashAdvanceUpdate(BaseModel):
    """Update cash advance request."""

    purpose: Optional[str] = Field(default=None, max_length=500)
    requested_amount: Optional[Decimal] = None
    expected_settlement_date: Optional[date] = None
    cost_center_id: Optional[UUID] = None
    advance_account_id: Optional[UUID] = None
    notes: Optional[str] = None


class CashAdvanceRead(CashAdvanceBase):
    """Cash advance response."""

    model_config = ConfigDict(from_attributes=True)

    advance_id: UUID
    organization_id: UUID
    advance_number: str
    approved_amount: Optional[Decimal] = None
    amount_settled: Decimal
    amount_refunded: Decimal
    disbursed_on: Optional[date] = None
    settled_on: Optional[date] = None
    status: CashAdvanceStatus
    approver_id: Optional[UUID] = None
    approved_on: Optional[date] = None
    rejection_reason: Optional[str] = None
    payment_mode: Optional[str] = None
    payment_reference: Optional[str] = None
    journal_entry_id: Optional[UUID] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    employee: Optional[EmployeeBrief] = None
    approver: Optional[EmployeeBrief] = None


class CashAdvanceListResponse(BaseModel):
    """Paginated cash advance list response."""

    items: List[CashAdvanceRead]
    total: int
    offset: int
    limit: int


class CashAdvanceApprovalRequest(BaseModel):
    """Approve/reject cash advance."""

    action: str = Field(description="APPROVE or REJECT")
    approved_amount: Optional[Decimal] = None
    rejection_reason: Optional[str] = None


class CashAdvanceDisburseRequest(BaseModel):
    """Disburse cash advance."""

    disbursed_amount: Decimal
    disbursement_date: date
    payment_reference: Optional[str] = Field(default=None, max_length=100)


class CashAdvanceSettleRequest(BaseModel):
    """Settle cash advance."""

    settled_amount: Decimal
    settlement_date: date
    notes: Optional[str] = None


class CashAdvanceRefundRequest(BaseModel):
    """Record refund from employee."""

    refund_amount: Decimal
    payment_reference: Optional[str] = Field(default=None, max_length=100)


# =============================================================================
# Corporate Card Schemas
# =============================================================================


class CorporateCardBase(BaseModel):
    """Base corporate card schema."""

    card_number_last4: str = Field(max_length=4)
    card_name: str = Field(max_length=100)
    card_type: str
    issuer: Optional[str] = Field(default=None, max_length=100)
    employee_id: UUID
    assigned_date: date
    expiry_date: Optional[date] = None
    credit_limit: Optional[Decimal] = None
    single_transaction_limit: Optional[Decimal] = None
    monthly_limit: Optional[Decimal] = None
    currency_code: str = "NGN"
    liability_account_id: Optional[UUID] = None


class CorporateCardCreate(CorporateCardBase):
    """Create corporate card request."""

    pass


class CorporateCardUpdate(BaseModel):
    """Update corporate card request."""

    card_name: Optional[str] = Field(default=None, max_length=100)
    expiry_date: Optional[date] = None
    credit_limit: Optional[Decimal] = None
    single_transaction_limit: Optional[Decimal] = None
    monthly_limit: Optional[Decimal] = None
    liability_account_id: Optional[UUID] = None


class CorporateCardRead(CorporateCardBase):
    """Corporate card response."""

    model_config = ConfigDict(from_attributes=True)

    card_id: UUID
    organization_id: UUID
    is_active: bool
    deactivated_on: Optional[date] = None
    deactivation_reason: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    employee: Optional[EmployeeBrief] = None


class CorporateCardListResponse(BaseModel):
    """Paginated corporate card list response."""

    items: List[CorporateCardRead]
    total: int
    offset: int
    limit: int


class DeactivateCardRequest(BaseModel):
    """Deactivate corporate card request."""

    reason: str = Field(max_length=200)


# =============================================================================
# Card Transaction Schemas
# =============================================================================


class CardTransactionBase(BaseModel):
    """Base card transaction schema."""

    card_id: UUID
    transaction_date: date
    posting_date: Optional[date] = None
    merchant_name: str = Field(max_length=200)
    merchant_category: Optional[str] = Field(default=None, max_length=100)
    amount: Decimal
    currency_code: str = "NGN"
    original_currency: Optional[str] = None
    original_amount: Optional[Decimal] = None
    external_reference: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = None
    notes: Optional[str] = None


class CardTransactionCreate(CardTransactionBase):
    """Create card transaction request."""

    pass


class CardTransactionUpdate(BaseModel):
    """Update card transaction request."""

    merchant_name: Optional[str] = Field(default=None, max_length=200)
    merchant_category: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[CardTransactionStatus] = None


class CorporateCardBrief(BaseModel):
    """Brief corporate card info."""

    model_config = ConfigDict(from_attributes=True)

    card_id: UUID
    card_name: str
    card_number_last4: str


class CardTransactionRead(CardTransactionBase):
    """Card transaction response."""

    model_config = ConfigDict(from_attributes=True)

    transaction_id: UUID
    organization_id: UUID
    status: CardTransactionStatus
    expense_claim_id: Optional[UUID] = None
    matched_on: Optional[date] = None
    is_personal_expense: bool
    personal_deduction_from_salary: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    card: Optional[CorporateCardBrief] = None


class CardTransactionListResponse(BaseModel):
    """Paginated card transaction list response."""

    items: List[CardTransactionRead]
    total: int
    offset: int
    limit: int


class MatchTransactionRequest(BaseModel):
    """Match card transaction to expense claim."""

    expense_claim_id: UUID


class MarkPersonalRequest(BaseModel):
    """Mark transaction as personal expense."""

    deduct_from_salary: bool = False


class BulkImportTransactionsRequest(BaseModel):
    """Bulk import card transactions."""

    card_id: UUID
    transactions: List[CardTransactionCreate]


class ExpenseStats(BaseModel):
    """Expense statistics for dashboard."""

    pending_claims: int
    total_pending_amount: Decimal
    claims_this_month: int
    amount_this_month: Decimal
    outstanding_advances: int
    advance_outstanding_amount: Decimal


class EmployeeExpenseSummary(BaseModel):
    """Employee expense summary."""

    employee_id: UUID
    claims_count: int
    total_claimed: Decimal
    total_approved: Decimal
    outstanding_advance: Decimal
