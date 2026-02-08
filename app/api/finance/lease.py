"""
LEASE API Router.

Lease Accounting API endpoints per IFRS 16.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.api.finance.utils import parse_enum
from app.db import SessionLocal
from app.models.finance.lease.lease_contract import LeaseClassification, LeaseStatus
from app.schemas.finance.common import ListResponse, PostingResultSchema
from app.services.auth_dependencies import require_tenant_permission
from app.services.feature_flags import FEATURE_LEASES, require_feature
from app.services.finance.lease import (
    LeaseContractInput,
    lease_calculation_service,
    lease_contract_service,
    lease_posting_adapter,
)

router = APIRouter(
    prefix="/lease",
    tags=["leases"],
    dependencies=[
        Depends(require_tenant_auth),
        Depends(require_feature(FEATURE_LEASES)),
    ],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =============================================================================
# Schemas
# =============================================================================


class LeaseContractCreate(BaseModel):
    """Create lease contract request."""

    lease_name: str = Field(max_length=200)
    lessor_name: str = Field(max_length=200)
    classification: str = "OPERATING"
    asset_description: str
    commencement_date: date
    end_date: date
    currency_code: str = Field(max_length=3)
    payment_frequency: str = "MONTHLY"
    base_payment_amount: Decimal
    incremental_borrowing_rate: Decimal
    lease_liability_account_id: UUID
    interest_expense_account_id: UUID
    rou_asset_account_id: UUID
    depreciation_expense_account_id: UUID
    description: str | None = None
    lessor_supplier_id: UUID | None = None
    external_reference: str | None = None
    is_lessee: bool = True
    payment_timing: str = "ADVANCE"
    has_renewal_option: bool = False
    renewal_option_term_months: int | None = None
    renewal_reasonably_certain: bool = False
    has_purchase_option: bool = False
    purchase_option_price: Decimal | None = None
    purchase_reasonably_certain: bool = False
    has_termination_option: bool = False
    termination_penalty: Decimal | None = None
    has_variable_payments: bool = False
    variable_payment_basis: str | None = None
    is_index_linked: bool = False
    index_type: str | None = None
    index_base_value: Decimal | None = None
    residual_value_guarantee: Decimal = Decimal("0")
    implicit_rate_known: bool = False
    implicit_rate: Decimal | None = None
    initial_direct_costs: Decimal = Decimal("0")
    lease_incentives_received: Decimal = Decimal("0")
    restoration_obligation: Decimal = Decimal("0")
    asset_category_id: UUID | None = None
    location_id: UUID | None = None
    cost_center_id: UUID | None = None
    project_id: UUID | None = None


class LeaseContractRead(BaseModel):
    """Lease contract response."""

    model_config = ConfigDict(from_attributes=True)

    lease_id: UUID
    organization_id: UUID
    lease_code: str
    lease_name: str
    lessor_name: str
    lease_type: str
    commencement_date: date
    end_date: date
    lease_term_months: int
    base_rent_amount: Decimal
    initial_rou_asset: Decimal
    initial_lease_liability: Decimal
    current_rou_asset: Decimal
    current_lease_liability: Decimal
    status: str


class LeaseCalculationRead(BaseModel):
    """Lease calculation result."""

    lease_id: UUID
    calculation_date: date
    present_value_payments: Decimal
    initial_direct_costs: Decimal
    rou_asset_value: Decimal
    lease_liability: Decimal
    monthly_depreciation: Decimal


class LeaseScheduleLineRead(BaseModel):
    """Lease amortization schedule line."""

    period_number: int
    payment_date: date
    payment_amount: Decimal
    interest_expense: Decimal
    principal_reduction: Decimal
    opening_liability: Decimal
    closing_liability: Decimal


class LeaseScheduleRead(BaseModel):
    """Complete lease amortization schedule."""

    lease_id: UUID
    lease_code: str
    total_payments: Decimal
    total_interest: Decimal
    total_principal: Decimal
    lines: list[LeaseScheduleLineRead]


class LeaseModificationCreate(BaseModel):
    """Lease modification request."""

    fiscal_period_id: UUID
    modification_date: date
    effective_date: date | None = None
    modification_type: str = Field(max_length=30)
    new_lease_payments: Decimal | None = None
    revised_discount_rate: Decimal | None = None
    revised_lease_term_months: int | None = None
    reason: str | None = None


# =============================================================================
# Lease Contracts
# =============================================================================


@router.post(
    "/contracts", response_model=LeaseContractRead, status_code=status.HTTP_201_CREATED
)
def create_lease_contract(
    payload: LeaseContractCreate,
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("lease:contracts:create")),
    db: Session = Depends(get_db),
):
    """Create a new lease contract."""
    classification_value = parse_enum(LeaseClassification, payload.classification)
    if classification_value is None:
        raise HTTPException(status_code=400, detail="Invalid lease classification")

    input_data = LeaseContractInput(
        lease_name=payload.lease_name,
        lessor_name=payload.lessor_name,
        classification=classification_value,
        commencement_date=payload.commencement_date,
        end_date=payload.end_date,
        currency_code=payload.currency_code,
        payment_frequency=payload.payment_frequency,
        base_payment_amount=payload.base_payment_amount,
        incremental_borrowing_rate=payload.incremental_borrowing_rate,
        asset_description=payload.asset_description,
        lease_liability_account_id=payload.lease_liability_account_id,
        interest_expense_account_id=payload.interest_expense_account_id,
        rou_asset_account_id=payload.rou_asset_account_id,
        depreciation_expense_account_id=payload.depreciation_expense_account_id,
        description=payload.description,
        lessor_supplier_id=payload.lessor_supplier_id,
        external_reference=payload.external_reference,
        is_lessee=payload.is_lessee,
        payment_timing=payload.payment_timing,
        has_renewal_option=payload.has_renewal_option,
        renewal_option_term_months=payload.renewal_option_term_months,
        renewal_reasonably_certain=payload.renewal_reasonably_certain,
        has_purchase_option=payload.has_purchase_option,
        purchase_option_price=payload.purchase_option_price,
        purchase_reasonably_certain=payload.purchase_reasonably_certain,
        has_termination_option=payload.has_termination_option,
        termination_penalty=payload.termination_penalty,
        has_variable_payments=payload.has_variable_payments,
        variable_payment_basis=payload.variable_payment_basis,
        is_index_linked=payload.is_index_linked,
        index_type=payload.index_type,
        index_base_value=payload.index_base_value,
        residual_value_guarantee=payload.residual_value_guarantee,
        implicit_rate_known=payload.implicit_rate_known,
        implicit_rate=payload.implicit_rate,
        initial_direct_costs=payload.initial_direct_costs,
        lease_incentives_received=payload.lease_incentives_received,
        restoration_obligation=payload.restoration_obligation,
        asset_category_id=payload.asset_category_id,
        location_id=payload.location_id,
        cost_center_id=payload.cost_center_id,
        project_id=payload.project_id,
    )
    return lease_contract_service.create_contract(
        db=db,
        organization_id=organization_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )


@router.get("/contracts/{lease_id}", response_model=LeaseContractRead)
def get_lease_contract(
    lease_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("lease:contracts:read")),
    db: Session = Depends(get_db),
):
    """Get a lease contract by ID."""
    return lease_contract_service.get(db, str(lease_id), organization_id)


@router.get("/contracts", response_model=ListResponse[LeaseContractRead])
def list_lease_contracts(
    organization_id: UUID = Depends(require_organization_id),
    classification: str | None = None,
    status: str | None = None,
    lessor_supplier_id: UUID | None = None,
    is_lessee: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("lease:contracts:read")),
    db: Session = Depends(get_db),
):
    """List lease contracts with filters."""
    leases = lease_contract_service.list(
        db=db,
        organization_id=str(organization_id),
        classification=parse_enum(LeaseClassification, classification),
        status=parse_enum(LeaseStatus, status),
        lessor_supplier_id=str(lessor_supplier_id) if lessor_supplier_id else None,
        is_lessee=is_lessee,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=leases,
        count=len(leases),
        limit=limit,
        offset=offset,
    )


@router.post("/contracts/{lease_id}/commence", response_model=LeaseContractRead)
def commence_lease(
    lease_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    commenced_by_user_id: UUID = Query(...),
    lease_liability_account_id: UUID = Query(...),
    interest_expense_account_id: UUID = Query(...),
    rou_asset_account_id: UUID = Query(...),
    depreciation_expense_account_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("lease:contracts:commence")),
    db: Session = Depends(get_db),
):
    """Commence a lease (activate)."""
    lease_contract_service.approve_contract(
        db=db,
        organization_id=organization_id,
        lease_id=lease_id,
        approved_by_user_id=commenced_by_user_id,
    )
    contract, _, _ = lease_contract_service.activate_contract(
        db=db,
        organization_id=organization_id,
        lease_id=lease_id,
        lease_liability_account_id=lease_liability_account_id,
        interest_expense_account_id=interest_expense_account_id,
        rou_asset_account_id=rou_asset_account_id,
        depreciation_expense_account_id=depreciation_expense_account_id,
    )
    return contract


@router.post("/contracts/{lease_id}/terminate", response_model=LeaseContractRead)
def terminate_lease(
    lease_id: UUID,
    termination_date: date = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    reason: str | None = None,
    auth: dict = Depends(require_tenant_permission("lease:contracts:terminate")),
    db: Session = Depends(get_db),
):
    """Terminate a lease early."""
    return lease_contract_service.terminate_contract(
        db=db,
        organization_id=organization_id,
        lease_id=lease_id,
        termination_date=termination_date,
        termination_reason=reason,
    )


# =============================================================================
# Lease Calculations
# =============================================================================


@router.post("/contracts/{lease_id}/calculate", response_model=LeaseCalculationRead)
def calculate_lease(
    lease_id: UUID,
    calculation_date: date = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("lease:calculations:calculate")),
    db: Session = Depends(get_db),
):
    """Calculate initial lease values (ROU asset, liability)."""
    contract = lease_contract_service.get(db, str(lease_id), organization_id)
    liability = lease_calculation_service.calculate_initial_liability(db, contract)
    rou_asset_value = (
        liability.total_liability
        + contract.initial_direct_costs
        - contract.lease_incentives_received
        + contract.restoration_obligation
    )
    try:
        monthly_depreciation = lease_calculation_service.calculate_rou_depreciation(
            db=db,
            lease_id=lease_id,
            periods=1,
        )
    except HTTPException:
        monthly_depreciation = Decimal("0")

    return LeaseCalculationRead(
        lease_id=lease_id,
        calculation_date=calculation_date,
        present_value_payments=liability.total_liability,
        initial_direct_costs=contract.initial_direct_costs,
        rou_asset_value=rou_asset_value,
        lease_liability=liability.total_liability,
        monthly_depreciation=monthly_depreciation,
    )


@router.get("/contracts/{lease_id}/schedule", response_model=LeaseScheduleRead)
def get_lease_schedule(
    lease_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("lease:calculations:read")),
    db: Session = Depends(get_db),
):
    """Get lease amortization schedule."""
    contract = lease_contract_service.get(db, str(lease_id), organization_id)
    schedule = lease_calculation_service.generate_amortization_schedule(
        db=db, lease_id=lease_id
    )
    lines = [
        LeaseScheduleLineRead(
            period_number=entry.period,
            payment_date=entry.payment_date,
            payment_amount=entry.payment_amount,
            interest_expense=entry.interest_expense,
            principal_reduction=entry.principal_reduction,
            opening_liability=entry.opening_balance,
            closing_liability=entry.closing_balance,
        )
        for entry in schedule
    ]
    total_payments = sum((line.payment_amount for line in lines), Decimal("0"))
    total_interest = sum((line.interest_expense for line in lines), Decimal("0"))
    total_principal = sum((line.principal_reduction for line in lines), Decimal("0"))

    return LeaseScheduleRead(
        lease_id=lease_id,
        lease_code=contract.lease_number,
        total_payments=total_payments,
        total_interest=total_interest,
        total_principal=total_principal,
        lines=lines,
    )


# =============================================================================
# Lease Postings
# =============================================================================


@router.post("/contracts/{lease_id}/post-initial", response_model=PostingResultSchema)
def post_initial_recognition(
    lease_id: UUID,
    posting_date: date = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    posted_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("lease:postings:post")),
    db: Session = Depends(get_db),
):
    """Post initial lease recognition to GL."""
    result = lease_posting_adapter.post_initial_recognition(
        db=db,
        organization_id=organization_id,
        lease_id=lease_id,
        posting_date=posting_date,
        posted_by_user_id=posted_by_user_id,
    )
    return PostingResultSchema(
        success=result.success,
        journal_entry_id=result.journal_entry_id,
        entry_number=None,
        message=result.message,
    )


@router.post("/contracts/{lease_id}/post-interest", response_model=PostingResultSchema)
def post_interest_accrual(
    lease_id: UUID,
    accrual_date: date = Query(...),
    interest_amount: Decimal = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    posted_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("lease:postings:post")),
    db: Session = Depends(get_db),
):
    """Post lease interest accrual to GL."""
    result = lease_posting_adapter.post_interest_accrual(
        db=db,
        organization_id=organization_id,
        lease_id=lease_id,
        accrual_date=accrual_date,
        interest_amount=interest_amount,
        posted_by_user_id=posted_by_user_id,
    )
    return PostingResultSchema(
        success=result.success,
        journal_entry_id=result.journal_entry_id,
        entry_number=None,
        message=result.message,
    )


@router.post("/contracts/{lease_id}/post-payment", response_model=PostingResultSchema)
def post_lease_payment(
    lease_id: UUID,
    payment_date: date = Query(...),
    payment_amount: Decimal = Query(...),
    cash_account_id: UUID = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    posted_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("lease:postings:post")),
    db: Session = Depends(get_db),
):
    """Post lease payment to GL."""
    result = lease_posting_adapter.post_lease_payment(
        db=db,
        organization_id=organization_id,
        lease_id=lease_id,
        payment_date=payment_date,
        payment_amount=payment_amount,
        cash_account_id=cash_account_id,
        posted_by_user_id=posted_by_user_id,
    )
    return PostingResultSchema(
        success=result.success,
        journal_entry_id=result.journal_entry_id,
        entry_number=None,
        message=result.message,
    )


@router.post(
    "/contracts/{lease_id}/post-depreciation", response_model=PostingResultSchema
)
def post_rou_depreciation(
    lease_id: UUID,
    depreciation_date: date = Query(...),
    depreciation_amount: Decimal = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    posted_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("lease:postings:post")),
    db: Session = Depends(get_db),
):
    """Post ROU asset depreciation to GL."""
    result = lease_posting_adapter.post_rou_depreciation(
        db=db,
        organization_id=organization_id,
        lease_id=lease_id,
        depreciation_date=depreciation_date,
        depreciation_amount=depreciation_amount,
        posted_by_user_id=posted_by_user_id,
    )
    return PostingResultSchema(
        success=result.success,
        journal_entry_id=result.journal_entry_id,
        entry_number=None,
        message=result.message,
    )


# =============================================================================
# Lease Modifications
# =============================================================================


class ModificationResultRead(BaseModel):
    """Modification result response."""

    success: bool
    modification_id: UUID | None = None
    liability_adjustment: Decimal = Decimal("0")
    rou_asset_adjustment: Decimal = Decimal("0")
    gain_loss: Decimal = Decimal("0")
    message: str = ""


@router.post("/contracts/{lease_id}/modify", response_model=ModificationResultRead)
def modify_lease(
    lease_id: UUID,
    payload: LeaseModificationCreate,
    organization_id: UUID = Depends(require_organization_id),
    modified_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("lease:modifications:create")),
    db: Session = Depends(get_db),
):
    """Modify an existing lease."""
    effective_date = payload.effective_date or payload.modification_date
    input_data = ModificationInput(
        lease_id=lease_id,
        fiscal_period_id=payload.fiscal_period_id,
        modification_date=payload.modification_date,
        effective_date=effective_date,
        modification_type=ModificationType(payload.modification_type),
        description=payload.reason,
        is_separate_lease=False,
        new_lease_payments=payload.new_lease_payments,
        revised_discount_rate=payload.revised_discount_rate,
        revised_lease_term_months=payload.revised_lease_term_months,
    )
    result = lease_modification_service.process_modification(
        db, organization_id, input_data, modified_by_user_id
    )
    return ModificationResultRead(
        success=result.success,
        modification_id=result.modification.modification_id
        if result.modification
        else None,
        liability_adjustment=result.liability_adjustment,
        rou_asset_adjustment=result.rou_asset_adjustment,
        gain_loss=result.gain_loss,
        message=result.message,
    )


# =============================================================================
# IFRS 16 Lease Modifications (Full Service)
# =============================================================================

from app.models.finance.lease.lease_modification import (  # noqa: E402
    ModificationType,
)
from app.services.finance.lease import (  # noqa: E402
    ModificationInput,
    lease_modification_service,
)


class FullModificationCreate(BaseModel):
    """Full lease modification input."""

    fiscal_period_id: UUID
    modification_date: date
    effective_date: date
    modification_type: str = "TERM_EXTENSION"
    description: str | None = None
    is_separate_lease: bool = False
    new_lease_payments: Decimal | None = None
    revised_discount_rate: Decimal | None = None
    revised_lease_term_months: int | None = None


class LeaseModificationRead(BaseModel):
    """Lease modification response."""

    model_config = ConfigDict(from_attributes=True)
    modification_id: UUID
    lease_id: UUID
    modification_date: date
    effective_date: date
    modification_type: str
    is_separate_lease: bool
    liability_before: Decimal
    liability_after: Decimal
    liability_adjustment: Decimal


@router.post("/modifications/{lease_id}", response_model=ModificationResultRead)
def process_lease_modification(
    lease_id: UUID,
    payload: FullModificationCreate,
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("lease:modifications:create")),
    db: Session = Depends(get_db),
):
    """Process a lease modification per IFRS 16."""
    input_data = ModificationInput(
        lease_id=lease_id,
        fiscal_period_id=payload.fiscal_period_id,
        modification_date=payload.modification_date,
        effective_date=payload.effective_date,
        modification_type=ModificationType(payload.modification_type),
        description=payload.description,
        is_separate_lease=payload.is_separate_lease,
        new_lease_payments=payload.new_lease_payments,
        revised_discount_rate=payload.revised_discount_rate,
        revised_lease_term_months=payload.revised_lease_term_months,
    )
    result = lease_modification_service.process_modification(
        db, organization_id, input_data, created_by_user_id
    )
    return ModificationResultRead(
        success=result.success,
        modification_id=result.modification.modification_id
        if result.modification
        else None,
        liability_adjustment=result.liability_adjustment,
        rou_asset_adjustment=result.rou_asset_adjustment,
        gain_loss=result.gain_loss,
        message=result.message,
    )


@router.get("/modifications", response_model=ListResponse[LeaseModificationRead])
def list_lease_modifications(
    organization_id: UUID = Depends(require_organization_id),
    lease_id: UUID | None = None,
    modification_type: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("lease:modifications:read")),
    db: Session = Depends(get_db),
):
    """List lease modifications with filters."""
    mod_type = ModificationType(modification_type) if modification_type else None
    modifications = lease_modification_service.list(
        db=db,
        organization_id=str(organization_id),
        modification_type=mod_type,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=modifications, count=len(modifications), limit=limit, offset=offset
    )


@router.post(
    "/modifications/{modification_id}/approve", response_model=LeaseModificationRead
)
def approve_lease_modification(
    modification_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    approved_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("lease:modifications:approve")),
    db: Session = Depends(get_db),
):
    """Approve a lease modification."""
    return lease_modification_service.approve_modification(
        db, organization_id, modification_id, approved_by_user_id
    )


# =============================================================================
# IFRS 16 Variable Payments & Index Adjustments
# =============================================================================

from app.services.finance.lease import (  # noqa: E402
    IndexAdjustmentInput,
    VariablePaymentInput,
    lease_variable_payment_service,
)


class VariablePaymentCreate(BaseModel):
    """Variable payment input."""

    schedule_id: UUID
    variable_amount: Decimal
    description: str | None = None


class IndexAdjustmentCreate(BaseModel):
    """Index adjustment input."""

    lease_id: UUID
    adjustment_date: date
    fiscal_period_id: UUID
    new_index_value: Decimal
    base_index_value: Decimal
    description: str | None = None


class PaymentScheduleRead(BaseModel):
    """Payment schedule response."""

    model_config = ConfigDict(from_attributes=True)
    schedule_id: UUID
    lease_id: UUID
    payment_number: int
    payment_date: date
    principal_portion: Decimal
    interest_portion: Decimal
    variable_payment: Decimal
    total_payment: Decimal
    status: str


class IndexAdjustmentResultRead(BaseModel):
    """Index adjustment result."""

    success: bool
    payments_adjusted: int = 0
    liability_adjustment: Decimal = Decimal("0")
    asset_adjustment: Decimal = Decimal("0")
    message: str = ""


@router.post("/variable-payments", response_model=PaymentScheduleRead)
def record_variable_payment(
    payload: VariablePaymentCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("lease:payments:create")),
    db: Session = Depends(get_db),
):
    """Record a variable payment on a scheduled payment."""
    input_data = VariablePaymentInput(
        schedule_id=payload.schedule_id,
        variable_amount=payload.variable_amount,
        description=payload.description,
    )
    return lease_variable_payment_service.record_variable_payment(
        db, organization_id, input_data
    )


@router.post("/index-adjustments", response_model=IndexAdjustmentResultRead)
def apply_index_adjustment(
    payload: IndexAdjustmentCreate,
    organization_id: UUID = Depends(require_organization_id),
    adjusted_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("lease:index:adjust")),
    db: Session = Depends(get_db),
):
    """Apply an index adjustment to a lease."""
    input_data = IndexAdjustmentInput(
        lease_id=payload.lease_id,
        adjustment_date=payload.adjustment_date,
        fiscal_period_id=payload.fiscal_period_id,
        new_index_value=payload.new_index_value,
        base_index_value=payload.base_index_value,
        description=payload.description,
    )
    result = lease_variable_payment_service.apply_index_adjustment(
        db, organization_id, input_data, adjusted_by_user_id
    )
    return IndexAdjustmentResultRead(
        success=result.success,
        payments_adjusted=result.payments_adjusted,
        liability_adjustment=result.liability_adjustment,
        asset_adjustment=result.asset_adjustment,
        message=result.message,
    )


@router.get("/schedules/overdue", response_model=ListResponse[PaymentScheduleRead])
def get_overdue_lease_payments(
    organization_id: UUID = Depends(require_organization_id),
    as_of_date: date | None = None,
    auth: dict = Depends(require_tenant_permission("lease:payments:read")),
    db: Session = Depends(get_db),
):
    """Get overdue lease payments."""
    overdue = lease_variable_payment_service.get_overdue_payments(
        db, organization_id, as_of_date
    )
    return ListResponse(items=overdue, count=len(overdue), limit=len(overdue), offset=0)


@router.get("/schedules/{lease_id}", response_model=ListResponse[PaymentScheduleRead])
def get_payment_schedules(
    lease_id: UUID,
    include_paid: bool = Query(default=False),
    auth: dict = Depends(require_tenant_permission("lease:payments:read")),
    db: Session = Depends(get_db),
):
    """Get scheduled payments for a lease."""
    schedules = lease_variable_payment_service.get_scheduled_payments(
        db, lease_id, include_paid
    )
    return ListResponse(
        items=schedules, count=len(schedules), limit=len(schedules), offset=0
    )


@router.post("/schedules/{schedule_id}/mark-paid", response_model=PaymentScheduleRead)
def mark_payment_paid(
    schedule_id: UUID,
    actual_payment_date: date = Query(...),
    actual_payment_amount: Decimal = Query(...),
    payment_reference: UUID | None = None,
    auth: dict = Depends(require_tenant_permission("lease:payments:update")),
    db: Session = Depends(get_db),
):
    """Mark a scheduled payment as paid."""
    return lease_variable_payment_service.mark_payment_paid(
        db, schedule_id, actual_payment_date, actual_payment_amount, payment_reference
    )
