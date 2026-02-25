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

from app.models.expense import (
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

    employee_id: UUID | None = None
    claim_date: date
    expense_period_start: date | None = None
    expense_period_end: date | None = None
    purpose: str = Field(max_length=500)
    project_id: UUID | None = None
    ticket_id: UUID | None = None
    task_id: UUID | None = None
    currency_code: str = "NGN"
    cost_center_id: UUID | None = None
    recipient_bank_code: str | None = None
    recipient_bank_name: str | None = None
    recipient_account_number: str | None = None
    recipient_account_name: str | None = None
    recipient_name: str | None = None
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
    recipient_bank_code: str | None = None
    recipient_bank_name: str | None = None
    recipient_account_number: str | None = None
    recipient_account_name: str | None = None
    recipient_name: str | None = None
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


class ExpenseClaimRejectRequest(BaseModel):
    """Reject expense claim request."""

    approver_id: UUID | None = None
    reason: str


class ExpenseClaimCancelRequest(BaseModel):
    """Cancel expense claim request."""

    reason: str | None = None


class LinkAdvanceRequest(BaseModel):
    """Link cash advance to expense claim."""

    advance_id: UUID
    amount_to_adjust: Decimal


class MarkPaidRequest(BaseModel):
    """Mark expense claim as paid."""

    payment_reference: str | None = None
    payment_date: date | None = None


# =============================================================================
# Cash Advance Schemas
# =============================================================================


class CashAdvanceBase(BaseModel):
    """Base cash advance schema."""

    employee_id: UUID | None = None
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

    disbursed_amount: Decimal | None = None
    disbursement_date: date | None = None
    payment_reference: str | None = Field(default=None, max_length=100)


class CashAdvanceSettleRequest(BaseModel):
    """Settle cash advance."""

    settled_amount: Decimal
    settlement_date: date | None = None
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
    employee_id: UUID | None = None
    assigned_date: date | None = None
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

    reason: str | None = Field(default=None, max_length=200)


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


# =============================================================================
# Expense Limit Rule Schemas
# =============================================================================


class DimensionFilters(BaseModel):
    """Dimension filters for expense limits."""

    category_ids: list[UUID] = []
    cost_center_ids: list[UUID] = []
    project_ids: list[UUID] = []
    is_cumulative: bool = True


class ActionConfig(BaseModel):
    """Action configuration for expense limits."""

    approver_id: UUID | None = None
    escalation_levels: list[int] = []
    min_approvers: int = 1
    warning_message: str | None = None


class ExpenseLimitRuleBase(BaseModel):
    """Base expense limit rule schema."""

    rule_code: str = Field(max_length=50)
    rule_name: str = Field(max_length=200)
    description: str | None = None
    scope_type: str = Field(
        description="EMPLOYEE, GRADE, DESIGNATION, DEPARTMENT, EMPLOYMENT_TYPE, ORGANIZATION"
    )
    scope_id: UUID | None = None
    period_type: str = Field(
        description="TRANSACTION, DAY, WEEK, MONTH, QUARTER, YEAR, CUSTOM"
    )
    custom_period_days: int | None = None
    limit_amount: Decimal
    currency_code: str = "NGN"
    action_type: str = Field(
        description="BLOCK, WARN, REQUIRE_APPROVAL, REQUIRE_MULTI_APPROVAL, AUTO_ESCALATE"
    )
    dimension_filters: DimensionFilters | None = None
    action_config: ActionConfig | None = None
    priority: int = 100
    effective_from: date
    effective_to: date | None = None
    is_active: bool = True


class ExpenseLimitRuleCreate(ExpenseLimitRuleBase):
    """Create expense limit rule request."""

    pass


class ExpenseLimitRuleUpdate(BaseModel):
    """Update expense limit rule request."""

    rule_name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    limit_amount: Decimal | None = None
    action_type: str | None = None
    dimension_filters: DimensionFilters | None = None
    action_config: ActionConfig | None = None
    priority: int | None = None
    effective_to: date | None = None
    is_active: bool | None = None


class ExpenseLimitRuleRead(ExpenseLimitRuleBase):
    """Expense limit rule response."""

    model_config = ConfigDict(from_attributes=True)

    rule_id: UUID
    organization_id: UUID
    evaluation_count: int = 0
    trigger_count: int = 0
    block_count: int = 0
    created_at: datetime
    updated_at: datetime | None = None


class ExpenseLimitRuleListResponse(BaseModel):
    """Paginated expense limit rule list response."""

    items: list[ExpenseLimitRuleRead]
    total: int
    offset: int
    limit: int


class ExpenseLimitRuleBrief(BaseModel):
    """Brief expense limit rule info."""

    model_config = ConfigDict(from_attributes=True)

    rule_id: UUID
    rule_code: str
    rule_name: str
    limit_amount: Decimal
    action_type: str


# =============================================================================
# Expense Approver Limit Schemas
# =============================================================================


class ExpenseApproverLimitBase(BaseModel):
    """Base expense approver limit schema."""

    scope_type: str = Field(description="EMPLOYEE, GRADE, DESIGNATION, ROLE")
    scope_id: UUID | None = None
    max_approval_amount: Decimal
    weekly_approval_budget: Decimal | None = None
    currency_code: str = "NGN"
    dimension_filters: DimensionFilters | None = None
    escalate_to_employee_id: UUID | None = None
    escalate_to_grade_min_rank: int | None = None
    can_approve_own_expenses: bool = False
    is_active: bool = True


class ExpenseApproverLimitCreate(ExpenseApproverLimitBase):
    """Create expense approver limit request."""

    pass


class ExpenseApproverLimitUpdate(BaseModel):
    """Update expense approver limit request."""

    max_approval_amount: Decimal | None = None
    weekly_approval_budget: Decimal | None = None
    dimension_filters: DimensionFilters | None = None
    escalate_to_employee_id: UUID | None = None
    escalate_to_grade_min_rank: int | None = None
    can_approve_own_expenses: bool | None = None
    is_active: bool | None = None


class ExpenseApproverLimitRead(ExpenseApproverLimitBase):
    """Expense approver limit response."""

    model_config = ConfigDict(from_attributes=True)

    approver_limit_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: datetime | None = None


class ExpenseApproverLimitListResponse(BaseModel):
    """Paginated expense approver limit list response."""

    items: list[ExpenseApproverLimitRead]
    total: int
    offset: int
    limit: int


# =============================================================================
# Expense Limit Evaluation Schemas
# =============================================================================


class ExpenseLimitEvaluationBase(BaseModel):
    """Base expense limit evaluation schema."""

    claim_id: UUID
    claim_amount: Decimal
    period_spent_amount: Decimal | None = None
    period_start: date | None = None
    period_end: date | None = None
    rule_id: UUID | None = None
    rule_code: str | None = None
    result: str = Field(
        description="PASSED, BLOCKED, WARNING, APPROVAL_REQUIRED, MULTI_APPROVAL_REQUIRED, ESCALATED"
    )
    result_message: str | None = None
    context_data: dict | None = None


class ExpenseLimitEvaluationRead(ExpenseLimitEvaluationBase):
    """Expense limit evaluation response."""

    model_config = ConfigDict(from_attributes=True)

    evaluation_id: UUID
    organization_id: UUID
    evaluated_at: datetime
    evaluated_by_id: UUID | None = None

    rule: ExpenseLimitRuleBrief | None = None


class ExpenseLimitEvaluationListResponse(BaseModel):
    """Paginated expense limit evaluation list response."""

    items: list[ExpenseLimitEvaluationRead]
    total: int
    offset: int
    limit: int


# =============================================================================
# Expense Period Usage Schemas
# =============================================================================


class ExpensePeriodUsageBase(BaseModel):
    """Base expense period usage schema."""

    employee_id: UUID
    period_type: str
    period_start: date
    period_end: date
    dimension_type: str | None = None
    dimension_id: UUID | None = None
    total_claimed: Decimal = Decimal("0")
    total_approved: Decimal = Decimal("0")
    claim_count: int = 0
    currency_code: str = "NGN"


class ExpensePeriodUsageRead(ExpensePeriodUsageBase):
    """Expense period usage response."""

    model_config = ConfigDict(from_attributes=True)

    usage_id: UUID
    organization_id: UUID
    last_calculated_at: datetime
    is_stale: bool = False


class ExpensePeriodUsageListResponse(BaseModel):
    """Paginated expense period usage list response."""

    items: list[ExpensePeriodUsageRead]
    total: int
    offset: int
    limit: int


# =============================================================================
# Limit Evaluation Request/Response Schemas
# =============================================================================


class EvaluateLimitRequest(BaseModel):
    """Request to evaluate expense limits for a claim."""

    claim_id: UUID
    preview_only: bool = False  # If true, don't record evaluation


class EvaluateLimitResponse(BaseModel):
    """Response from expense limit evaluation."""

    claim_id: UUID
    claim_amount: Decimal
    result: str  # PASSED, BLOCKED, WARNING, APPROVAL_REQUIRED, etc.
    result_message: str | None = None
    triggered_rules: list[ExpenseLimitRuleBrief] = []
    period_usage: ExpensePeriodUsageRead | None = None
    eligible_approvers: list["EligibleApprover"] = []


class EligibleApprover(BaseModel):
    """Eligible approver for an expense claim."""

    model_config = ConfigDict(from_attributes=True)

    employee_id: UUID
    employee_name: str
    max_approval_amount: Decimal
    is_direct_manager: bool = False
    grade_rank: int | None = None


class EmployeeUsageSummary(BaseModel):
    """Summary of an employee's expense usage across periods."""

    employee_id: UUID
    employee_name: str | None = None
    current_month_claimed: Decimal = Decimal("0")
    current_month_approved: Decimal = Decimal("0")
    current_quarter_claimed: Decimal = Decimal("0")
    current_year_claimed: Decimal = Decimal("0")
    pending_claims_count: int = 0
    pending_claims_amount: Decimal = Decimal("0")
    applicable_limits: list[ExpenseLimitRuleBrief] = []
