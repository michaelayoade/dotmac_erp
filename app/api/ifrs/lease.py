"""
LEASE API Router.

Lease Accounting API endpoints per IFRS 16.
"""

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.schemas.ifrs.common import ListResponse, PostingResultSchema
from app.services.ifrs.lease import (
    lease_contract_service,
    lease_calculation_service,
    lease_posting_adapter,
    LeaseContractInput,
)


router = APIRouter(prefix="/lease", tags=["leases"])


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

    lease_code: str = Field(max_length=30)
    lease_name: str = Field(max_length=200)
    lessor_name: str = Field(max_length=200)
    asset_description: str
    lease_type: str = "OPERATING"
    commencement_date: date
    end_date: date
    lease_term_months: int
    currency_code: str = Field(max_length=3)
    base_rent_amount: Decimal
    payment_frequency: str = "MONTHLY"
    discount_rate: Decimal
    rou_asset_account_id: Optional[UUID] = None
    lease_liability_account_id: Optional[UUID] = None
    interest_expense_account_id: Optional[UUID] = None
    depreciation_expense_account_id: Optional[UUID] = None


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

    modification_date: date
    modification_type: str = Field(max_length=30)
    new_lease_term_months: Optional[int] = None
    new_base_rent_amount: Optional[Decimal] = None
    new_discount_rate: Optional[Decimal] = None
    reason: Optional[str] = None


# =============================================================================
# Lease Contracts
# =============================================================================

@router.post("/contracts", response_model=LeaseContractRead, status_code=status.HTTP_201_CREATED)
def create_lease_contract(
    payload: LeaseContractCreate,
    organization_id: UUID = Query(...),
    created_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Create a new lease contract."""
    input_data = LeaseContractInput(
        lease_code=payload.lease_code,
        lease_name=payload.lease_name,
        lessor_name=payload.lessor_name,
        asset_description=payload.asset_description,
        lease_type=payload.lease_type,
        commencement_date=payload.commencement_date,
        end_date=payload.end_date,
        lease_term_months=payload.lease_term_months,
        currency_code=payload.currency_code,
        base_rent_amount=payload.base_rent_amount,
        payment_frequency=payload.payment_frequency,
        discount_rate=payload.discount_rate,
        rou_asset_account_id=payload.rou_asset_account_id,
        lease_liability_account_id=payload.lease_liability_account_id,
        interest_expense_account_id=payload.interest_expense_account_id,
        depreciation_expense_account_id=payload.depreciation_expense_account_id,
    )
    return lease_contract_service.create_lease(
        db=db,
        organization_id=organization_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )


@router.get("/contracts/{lease_id}", response_model=LeaseContractRead)
def get_lease_contract(
    lease_id: UUID,
    db: Session = Depends(get_db),
):
    """Get a lease contract by ID."""
    return lease_contract_service.get(db, str(lease_id))


@router.get("/contracts", response_model=ListResponse[LeaseContractRead])
def list_lease_contracts(
    organization_id: UUID = Query(...),
    classification: Optional[str] = None,
    status: Optional[str] = None,
    lessor_supplier_id: Optional[UUID] = None,
    is_lessee: Optional[bool] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List lease contracts with filters."""
    from app.models.ifrs.lease.lease_contract import LeaseClassification, LeaseStatus

    # Convert string parameters to enums if provided
    classification_enum = LeaseClassification(classification.upper()) if classification else None
    status_enum = LeaseStatus(status.upper()) if status else None

    leases = lease_contract_service.list(
        db=db,
        organization_id=str(organization_id),
        classification=classification_enum,
        status=status_enum,
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
    organization_id: UUID = Query(...),
    commenced_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Commence a lease (activate)."""
    return lease_contract_service.commence_lease(
        db=db,
        organization_id=organization_id,
        lease_id=lease_id,
        commenced_by_user_id=commenced_by_user_id,
    )


@router.post("/contracts/{lease_id}/terminate", response_model=LeaseContractRead)
def terminate_lease(
    lease_id: UUID,
    termination_date: date = Query(...),
    organization_id: UUID = Query(...),
    terminated_by_user_id: UUID = Query(...),
    reason: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Terminate a lease early."""
    return lease_contract_service.terminate_lease(
        db=db,
        organization_id=organization_id,
        lease_id=lease_id,
        termination_date=termination_date,
        terminated_by_user_id=terminated_by_user_id,
        reason=reason,
    )


# =============================================================================
# Lease Calculations
# =============================================================================

@router.post("/contracts/{lease_id}/calculate", response_model=LeaseCalculationRead)
def calculate_lease(
    lease_id: UUID,
    calculation_date: date = Query(...),
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Calculate initial lease values (ROU asset, liability)."""
    return lease_calculation_service.calculate_initial_values(
        db=db,
        organization_id=organization_id,
        lease_id=lease_id,
        calculation_date=calculation_date,
    )


@router.get("/contracts/{lease_id}/schedule", response_model=LeaseScheduleRead)
def get_lease_schedule(
    lease_id: UUID,
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Get lease amortization schedule."""
    return lease_calculation_service.get_amortization_schedule(
        db=db,
        organization_id=str(organization_id),
        lease_id=str(lease_id),
    )


# =============================================================================
# Lease Postings
# =============================================================================

@router.post("/contracts/{lease_id}/post-initial", response_model=PostingResultSchema)
def post_initial_recognition(
    lease_id: UUID,
    posting_date: date = Query(...),
    organization_id: UUID = Query(...),
    posted_by_user_id: UUID = Query(...),
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
        entry_number=result.entry_number,
        message=result.message,
    )


@router.post("/contracts/{lease_id}/post-interest", response_model=PostingResultSchema)
def post_interest_accrual(
    lease_id: UUID,
    accrual_date: date = Query(...),
    interest_amount: Decimal = Query(...),
    organization_id: UUID = Query(...),
    posted_by_user_id: UUID = Query(...),
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
        entry_number=result.entry_number,
        message=result.message,
    )


@router.post("/contracts/{lease_id}/post-payment", response_model=PostingResultSchema)
def post_lease_payment(
    lease_id: UUID,
    payment_date: date = Query(...),
    payment_amount: Decimal = Query(...),
    cash_account_id: UUID = Query(...),
    organization_id: UUID = Query(...),
    posted_by_user_id: UUID = Query(...),
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
        entry_number=result.entry_number,
        message=result.message,
    )


@router.post("/contracts/{lease_id}/post-depreciation", response_model=PostingResultSchema)
def post_rou_depreciation(
    lease_id: UUID,
    depreciation_date: date = Query(...),
    depreciation_amount: Decimal = Query(...),
    organization_id: UUID = Query(...),
    posted_by_user_id: UUID = Query(...),
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
        entry_number=result.entry_number,
        message=result.message,
    )


# =============================================================================
# Lease Modifications
# =============================================================================

@router.post("/contracts/{lease_id}/modify", response_model=LeaseContractRead)
def modify_lease(
    lease_id: UUID,
    payload: LeaseModificationCreate,
    organization_id: UUID = Query(...),
    modified_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Modify an existing lease."""
    return lease_contract_service.modify_lease(
        db=db,
        organization_id=organization_id,
        lease_id=lease_id,
        modification_date=payload.modification_date,
        modification_type=payload.modification_type,
        new_lease_term_months=payload.new_lease_term_months,
        new_base_rent_amount=payload.new_base_rent_amount,
        new_discount_rate=payload.new_discount_rate,
        reason=payload.reason,
        modified_by_user_id=modified_by_user_id,
    )


# =============================================================================
# IFRS 16 Lease Modifications (Full Service)
# =============================================================================

from app.services.ifrs.lease import (
    lease_modification_service,
    ModificationInput,
)
from app.models.ifrs.lease.lease_modification import ModificationType


class FullModificationCreate(BaseModel):
    """Full lease modification input."""
    fiscal_period_id: UUID
    modification_date: date
    effective_date: date
    modification_type: str = "TERM_EXTENSION"
    description: Optional[str] = None
    is_separate_lease: bool = False
    new_lease_payments: Optional[Decimal] = None
    revised_discount_rate: Optional[Decimal] = None
    revised_lease_term_months: Optional[int] = None


class ModificationResultRead(BaseModel):
    """Modification result response."""
    success: bool
    modification_id: Optional[UUID] = None
    liability_adjustment: Decimal = Decimal("0")
    rou_asset_adjustment: Decimal = Decimal("0")
    gain_loss: Decimal = Decimal("0")
    message: str = ""


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
    organization_id: UUID = Query(...),
    created_by_user_id: UUID = Query(...),
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
        modification_id=result.modification.modification_id if result.modification else None,
        liability_adjustment=result.liability_adjustment,
        rou_asset_adjustment=result.rou_asset_adjustment,
        gain_loss=result.gain_loss,
        message=result.message,
    )


@router.get("/modifications", response_model=ListResponse[LeaseModificationRead])
def list_lease_modifications(
    organization_id: UUID = Query(...),
    lease_id: Optional[UUID] = None,
    modification_type: Optional[str] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
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
    return ListResponse(items=modifications, count=len(modifications), limit=limit, offset=offset)


@router.post("/modifications/{modification_id}/approve", response_model=LeaseModificationRead)
def approve_lease_modification(
    modification_id: UUID,
    organization_id: UUID = Query(...),
    approved_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Approve a lease modification."""
    return lease_modification_service.approve_modification(
        db, organization_id, modification_id, approved_by_user_id
    )


# =============================================================================
# IFRS 16 Variable Payments & Index Adjustments
# =============================================================================

from app.services.ifrs.lease import (
    lease_variable_payment_service,
    VariablePaymentInput,
    IndexAdjustmentInput,
)


class VariablePaymentCreate(BaseModel):
    """Variable payment input."""
    schedule_id: UUID
    variable_amount: Decimal
    description: Optional[str] = None


class IndexAdjustmentCreate(BaseModel):
    """Index adjustment input."""
    lease_id: UUID
    adjustment_date: date
    fiscal_period_id: UUID
    new_index_value: Decimal
    base_index_value: Decimal
    description: Optional[str] = None


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
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Record a variable payment on a scheduled payment."""
    input_data = VariablePaymentInput(
        schedule_id=payload.schedule_id,
        variable_amount=payload.variable_amount,
        description=payload.description,
    )
    return lease_variable_payment_service.record_variable_payment(db, organization_id, input_data)


@router.post("/index-adjustments", response_model=IndexAdjustmentResultRead)
def apply_index_adjustment(
    payload: IndexAdjustmentCreate,
    organization_id: UUID = Query(...),
    adjusted_by_user_id: UUID = Query(...),
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


@router.get("/schedules/{lease_id}", response_model=ListResponse[PaymentScheduleRead])
def get_payment_schedules(
    lease_id: UUID,
    include_paid: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """Get scheduled payments for a lease."""
    schedules = lease_variable_payment_service.get_scheduled_payments(db, lease_id, include_paid)
    return ListResponse(items=schedules, count=len(schedules), limit=len(schedules), offset=0)


@router.post("/schedules/{schedule_id}/mark-paid", response_model=PaymentScheduleRead)
def mark_payment_paid(
    schedule_id: UUID,
    actual_payment_date: date = Query(...),
    actual_payment_amount: Decimal = Query(...),
    payment_reference: Optional[UUID] = None,
    db: Session = Depends(get_db),
):
    """Mark a scheduled payment as paid."""
    return lease_variable_payment_service.mark_payment_paid(
        db, schedule_id, actual_payment_date, actual_payment_amount, payment_reference
    )


@router.get("/schedules/overdue", response_model=ListResponse[PaymentScheduleRead])
def get_overdue_lease_payments(
    organization_id: UUID = Query(...),
    as_of_date: Optional[date] = None,
    db: Session = Depends(get_db),
):
    """Get overdue lease payments."""
    overdue = lease_variable_payment_service.get_overdue_payments(db, organization_id, as_of_date)
    return ListResponse(items=overdue, count=len(overdue), limit=len(overdue), offset=0)
