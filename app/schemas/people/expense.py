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
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.people.exp import (
    CardTransactionStatus,
    CashAdvanceStatus,
    ExpenseClaimStatus,
)

# =============================================================================
# Expense Category Schemas
# =============================================================================


class ExpenseCategoryBase(BaseModel):
    """Base expense category schema."""

    category_code: str = Field(max_length=30)
    category_name: str = Field(max_length=200)
    description: str | None = None
    expense_account_id: UUID | None = None
    max_amount_per_claim: Decimal | None = None
    requires_receipt: bool = True
    is_active: bool = True


class ExpenseCategoryCreate(ExpenseCategoryBase):
    """Create expense category request."""

    pass


class ExpenseCategoryUpdate(BaseModel):
    """Update expense category request."""

    category_code: str | None = Field(default=None, max_length=30)
    category_name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    expense_account_id: UUID | None = None
    max_amount_per_claim: Decimal | None = None
    requires_receipt: bool | None = None
    is_active: bool | None = None


class ExpenseCategoryRead(ExpenseCategoryBase):
    """Expense category response."""

    model_config = ConfigDict(from_attributes=True)

    category_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: datetime | None = None


class ExpenseCategoryListResponse(BaseModel):
    """Paginated expense category list response."""

    items: list[ExpenseCategoryRead]
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
    expense_account_id: UUID | None = None
    cost_center_id: UUID | None = None
    receipt_url: str | None = Field(default=None, max_length=500)
    receipt_number: str | None = Field(default=None, max_length=50)
    vendor_name: str | None = Field(default=None, max_length=200)
    is_travel_expense: bool = False
    travel_from: str | None = Field(default=None, max_length=200)
    travel_to: str | None = Field(default=None, max_length=200)
    distance_km: Decimal | None = None
    notes: str | None = None


class ExpenseClaimItemCreate(ExpenseClaimItemBase):
    """Create expense claim item request."""

    pass


class ExpenseClaimItemRead(ExpenseClaimItemBase):
    """Expense claim item response."""

    model_config = ConfigDict(from_attributes=True)

    item_id: UUID
    organization_id: UUID
    claim_id: UUID
    approved_amount: Decimal | None = None
    sequence: int

    category: ExpenseCategoryBrief | None = None


# =============================================================================
# Expense Claim Schemas
# =============================================================================


class ExpenseClaimBase(BaseModel):
    """Base expense claim schema."""

    employee_id: UUID
    claim_date: date
    expense_period_start: date | None = None
    expense_period_end: date | None = None
    purpose: str = Field(max_length=500)
    project_id: UUID | None = None
    ticket_id: UUID | None = None
    task_id: UUID | None = None
    currency_code: str = "NGN"
    cost_center_id: UUID | None = None
    recipient_bank_code: str | None = Field(default=None, max_length=20)
    recipient_bank_name: str | None = Field(default=None, max_length=100)
    recipient_account_number: str | None = Field(default=None, max_length=20)
    recipient_name: str | None = Field(default=None, max_length=150)
    requested_approver_id: UUID | None = None
    notes: str | None = None


class ExpenseClaimCreate(ExpenseClaimBase):
    """Create expense claim request."""

    items: list[ExpenseClaimItemCreate] = []


class ExpenseClaimUpdate(BaseModel):
    """Update expense claim request."""

    claim_date: date | None = None
    expense_period_start: date | None = None
    expense_period_end: date | None = None
    purpose: str | None = Field(default=None, max_length=500)
    project_id: UUID | None = None
    ticket_id: UUID | None = None
    task_id: UUID | None = None
    cost_center_id: UUID | None = None
    recipient_bank_code: str | None = Field(default=None, max_length=20)
    recipient_bank_name: str | None = Field(default=None, max_length=100)
    recipient_account_number: str | None = Field(default=None, max_length=20)
    recipient_name: str | None = Field(default=None, max_length=150)
    requested_approver_id: UUID | None = None
    notes: str | None = None


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
    total_approved_amount: Decimal | None = None
    advance_adjusted: Decimal
    cash_advance_id: UUID | None = None
    net_payable_amount: Decimal | None = None
    status: ExpenseClaimStatus
    approver_id: UUID | None = None
    approved_on: date | None = None
    rejection_reason: str | None = None
    supplier_invoice_id: UUID | None = None
    payment_reference: str | None = None
    paid_on: date | None = None
    created_at: datetime
    updated_at: datetime | None = None

    employee: EmployeeBrief | None = None
    approver: EmployeeBrief | None = None
    items: list[ExpenseClaimItemRead] = []


class ExpenseClaimListResponse(BaseModel):
    """Paginated expense claim list response."""

    items: list[ExpenseClaimRead]
    total: int
    offset: int
    limit: int


class ExpenseClaimSubmitRequest(BaseModel):
    """Submit expense claim for approval."""

    pass


class ExpenseClaimApprovalRequest(BaseModel):
    """Approve expense claim."""

    approver_id: UUID | None = None
    notes: str | None = None
    approved_amounts: list["ItemApprovalAmount"] | None = None


class ItemApprovalAmount(BaseModel):
    """Approved amount for a claim item."""

    item_id: UUID
    approved_amount: Decimal


class ExpenseClaimCancelRequest(BaseModel):
    """Cancel expense claim request."""

    reason: str | None = None


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
    expected_settlement_date: date | None = None
    cost_center_id: UUID | None = None
    advance_account_id: UUID | None = None
    notes: str | None = None


class CashAdvanceCreate(CashAdvanceBase):
    """Create cash advance request."""

    pass


class CashAdvanceUpdate(BaseModel):
    """Update cash advance request."""

    purpose: str | None = Field(default=None, max_length=500)
    requested_amount: Decimal | None = None
    expected_settlement_date: date | None = None
    cost_center_id: UUID | None = None
    advance_account_id: UUID | None = None
    notes: str | None = None


class CashAdvanceRead(CashAdvanceBase):
    """Cash advance response."""

    model_config = ConfigDict(from_attributes=True)

    advance_id: UUID
    organization_id: UUID
    advance_number: str
    approved_amount: Decimal | None = None
    amount_settled: Decimal
    amount_refunded: Decimal
    disbursed_on: date | None = None
    settled_on: date | None = None
    status: CashAdvanceStatus
    approver_id: UUID | None = None
    approved_on: date | None = None
    rejection_reason: str | None = None
    payment_mode: str | None = None
    payment_reference: str | None = None
    journal_entry_id: UUID | None = None
    created_at: datetime
    updated_at: datetime | None = None

    employee: EmployeeBrief | None = None
    approver: EmployeeBrief | None = None


class CashAdvanceListResponse(BaseModel):
    """Paginated cash advance list response."""

    items: list[CashAdvanceRead]
    total: int
    offset: int
    limit: int


class CashAdvanceApprovalRequest(BaseModel):
    """Approve/reject cash advance."""

    action: str = Field(description="APPROVE or REJECT")
    approved_amount: Decimal | None = None
    rejection_reason: str | None = None


class CashAdvanceDisburseRequest(BaseModel):
    """Disburse cash advance."""

    disbursed_amount: Decimal
    disbursement_date: date
    payment_reference: str | None = Field(default=None, max_length=100)


class CashAdvanceSettleRequest(BaseModel):
    """Settle cash advance."""

    settled_amount: Decimal
    settlement_date: date
    notes: str | None = None


class CashAdvanceRefundRequest(BaseModel):
    """Record refund from employee."""

    refund_amount: Decimal
    payment_reference: str | None = Field(default=None, max_length=100)


# =============================================================================
# Corporate Card Schemas
# =============================================================================


class CorporateCardBase(BaseModel):
    """Base corporate card schema."""

    card_number_last4: str = Field(max_length=4)
    card_name: str = Field(max_length=100)
    card_type: str
    issuer: str | None = Field(default=None, max_length=100)
    employee_id: UUID
    assigned_date: date
    expiry_date: date | None = None
    credit_limit: Decimal | None = None
    single_transaction_limit: Decimal | None = None
    monthly_limit: Decimal | None = None
    currency_code: str = "NGN"
    liability_account_id: UUID | None = None


class CorporateCardCreate(CorporateCardBase):
    """Create corporate card request."""

    pass


class CorporateCardUpdate(BaseModel):
    """Update corporate card request."""

    card_name: str | None = Field(default=None, max_length=100)
    expiry_date: date | None = None
    credit_limit: Decimal | None = None
    single_transaction_limit: Decimal | None = None
    monthly_limit: Decimal | None = None
    liability_account_id: UUID | None = None


class CorporateCardRead(CorporateCardBase):
    """Corporate card response."""

    model_config = ConfigDict(from_attributes=True)

    card_id: UUID
    organization_id: UUID
    is_active: bool
    deactivated_on: date | None = None
    deactivation_reason: str | None = None
    created_at: datetime
    updated_at: datetime | None = None

    employee: EmployeeBrief | None = None


class CorporateCardListResponse(BaseModel):
    """Paginated corporate card list response."""

    items: list[CorporateCardRead]
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
    posting_date: date | None = None
    merchant_name: str = Field(max_length=200)
    merchant_category: str | None = Field(default=None, max_length=100)
    amount: Decimal
    currency_code: str = "NGN"
    original_currency: str | None = None
    original_amount: Decimal | None = None
    external_reference: str | None = Field(default=None, max_length=100)
    description: str | None = None
    notes: str | None = None


class CardTransactionCreate(CardTransactionBase):
    """Create card transaction request."""

    pass


class CardTransactionUpdate(BaseModel):
    """Update card transaction request."""

    merchant_name: str | None = Field(default=None, max_length=200)
    merchant_category: str | None = Field(default=None, max_length=100)
    description: str | None = None
    notes: str | None = None
    status: CardTransactionStatus | None = None


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
    expense_claim_id: UUID | None = None
    matched_on: date | None = None
    is_personal_expense: bool
    personal_deduction_from_salary: bool
    created_at: datetime
    updated_at: datetime | None = None

    card: CorporateCardBrief | None = None


class CardTransactionListResponse(BaseModel):
    """Paginated card transaction list response."""

    items: list[CardTransactionRead]
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
    transactions: list[CardTransactionCreate]


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
