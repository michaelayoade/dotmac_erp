"""
Payroll API Router.

Thin API wrapper for Payroll endpoints. All business logic is in services.
"""
from datetime import date
from typing import Optional
import csv
import io
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.db import SessionLocal
from app.schemas.people.payroll import (
    # Salary Component
    SalaryComponentCreate,
    SalaryComponentUpdate,
    SalaryComponentRead,
    SalaryComponentListResponse,
    # Salary Structure
    SalaryStructureCreate,
    SalaryStructureUpdate,
    SalaryStructureRead,
    SalaryStructureListResponse,
    SalaryStructureAssignmentCreate,
    SalaryStructureAssignmentUpdate,
    SalaryStructureAssignmentRead,
    SalaryStructureAssignmentListResponse,
    # Salary Slip
    SalarySlipCreate,
    SalarySlipRead,
    SalarySlipListResponse,
    SalarySlipPostRequest,
    SalarySlipPostResponse,
    PayrollEntryUpdate,
    PayrollEntryListResponse,
    PayrollSlipGenerationResult,
    PayrollPayoutRequest,
    PayrollPayoutResult,
    # Payroll Entry
    PayrollEntryCreate,
    PayrollEntryRead,
)
from app.models.people.payroll.salary_component import SalaryComponent, SalaryComponentType
from app.models.people.payroll.salary_structure import PayrollFrequency
from app.models.people.payroll.salary_structure import SalaryStructure
from app.models.people.payroll.salary_slip import SalarySlip, SalarySlipStatus
from app.services.people.payroll import (
    salary_slip_service,
    SalarySlipInput,
    payroll_gl_adapter,
    PayrollService,
)
from app.services.common import PaginationParams, coerce_uuid
from app.models.people.payroll.payroll_entry import PayrollEntryStatus

router = APIRouter(
    prefix="/payroll",
    tags=["payroll"],
    dependencies=[Depends(require_tenant_auth)],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def csv_response(rows: list[list[str]], filename: str) -> Response:
    """Return a CSV response for export endpoints."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerows(rows)
    content = buffer.getvalue()
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# =============================================================================
# Salary Components
# =============================================================================


@router.get("/components", response_model=SalaryComponentListResponse)
def list_salary_components(
    organization_id: UUID = Depends(require_organization_id),
    component_type: Optional[SalaryComponentType] = None,
    is_active: Optional[bool] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List salary components."""
    org_id = coerce_uuid(organization_id)

    query = db.query(SalaryComponent).filter(SalaryComponent.organization_id == org_id)

    if component_type:
        query = query.filter(SalaryComponent.component_type == component_type)

    if is_active is not None:
        query = query.filter(SalaryComponent.is_active == is_active)

    total = query.count()
    items = query.order_by(SalaryComponent.display_order).offset(offset).limit(limit).all()

    return SalaryComponentListResponse(
        items=[SalaryComponentRead.model_validate(c) for c in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post("/components", response_model=SalaryComponentRead, status_code=status.HTTP_201_CREATED)
def create_salary_component(
    data: SalaryComponentCreate,
    db: Session = Depends(get_db),
):
    """Create a new salary component."""
    component = SalaryComponent(
        organization_id=data.organization_id,
        component_code=data.component_code,
        component_name=data.component_name,
        abbr=data.abbr,
        component_type=data.component_type,
        description=data.description,
        expense_account_id=data.expense_account_id,
        liability_account_id=data.liability_account_id,
        is_tax_applicable=data.is_tax_applicable,
        is_statutory=data.is_statutory,
        depends_on_payment_days=data.depends_on_payment_days,
        is_active=True,
    )
    db.add(component)
    db.commit()
    db.refresh(component)

    return SalaryComponentRead.model_validate(component)


@router.get("/components/{component_id}", response_model=SalaryComponentRead)
def get_salary_component(
    component_id: UUID,
    db: Session = Depends(get_db),
):
    """Get a salary component by ID."""
    component = db.get(SalaryComponent, component_id)
    if not component:
        raise HTTPException(status_code=404, detail="Salary component not found")

    return SalaryComponentRead.model_validate(component)


@router.patch("/components/{component_id}", response_model=SalaryComponentRead)
def update_salary_component(
    component_id: UUID,
    data: SalaryComponentUpdate,
    db: Session = Depends(get_db),
):
    """Update a salary component."""
    component = db.get(SalaryComponent, component_id)
    if not component:
        raise HTTPException(status_code=404, detail="Salary component not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(component, key, value)

    db.commit()
    db.refresh(component)

    return SalaryComponentRead.model_validate(component)


# =============================================================================
# Salary Structures
# =============================================================================


@router.get("/structures", response_model=SalaryStructureListResponse)
def list_salary_structures(
    organization_id: UUID = Depends(require_organization_id),
    search: Optional[str] = None,
    is_active: Optional[bool] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List salary structures."""
    svc = PayrollService(db)
    result = svc.list_salary_structures(
        org_id=organization_id,
        search=search,
        is_active=is_active,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return SalaryStructureListResponse(
        items=[SalaryStructureRead.model_validate(s) for s in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post("/structures", response_model=SalaryStructureRead, status_code=status.HTTP_201_CREATED)
def create_salary_structure(
    data: SalaryStructureCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a salary structure."""
    svc = PayrollService(db)
    structure = svc.create_salary_structure(
        org_id=organization_id,
        structure_code=data.structure_code,
        structure_name=data.structure_name,
        description=data.description,
        payroll_frequency=data.payroll_frequency,
        currency_code=data.currency_code,
        earnings=[line.model_dump() for line in data.earnings],
        deductions=[line.model_dump() for line in data.deductions],
    )
    db.commit()
    return SalaryStructureRead.model_validate(structure)


@router.get("/structures/{structure_id}", response_model=SalaryStructureRead)
def get_salary_structure(
    structure_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a salary structure by ID."""
    svc = PayrollService(db)
    structure = svc.get_salary_structure(organization_id, structure_id)
    return SalaryStructureRead.model_validate(structure)


@router.patch("/structures/{structure_id}", response_model=SalaryStructureRead)
def update_salary_structure(
    structure_id: UUID,
    data: SalaryStructureUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a salary structure."""
    svc = PayrollService(db)
    update_data = data.model_dump(exclude_unset=True)
    structure = svc.update_salary_structure(
        organization_id,
        structure_id,
        **update_data,
    )
    db.commit()
    return SalaryStructureRead.model_validate(structure)


@router.delete("/structures/{structure_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_salary_structure(
    structure_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete a salary structure."""
    svc = PayrollService(db)
    svc.delete_salary_structure(organization_id, structure_id)
    db.commit()


# =============================================================================
# Salary Structure Assignments
# =============================================================================


@router.get("/assignments", response_model=SalaryStructureAssignmentListResponse)
def list_salary_assignments(
    organization_id: UUID = Depends(require_organization_id),
    employee_id: Optional[UUID] = None,
    structure_id: Optional[UUID] = None,
    active_on: Optional[date] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List salary structure assignments."""
    svc = PayrollService(db)
    result = svc.list_assignments(
        org_id=organization_id,
        employee_id=employee_id,
        structure_id=structure_id,
        active_on=active_on,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return SalaryStructureAssignmentListResponse(
        items=[SalaryStructureAssignmentRead.model_validate(a) for a in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post("/assignments", response_model=SalaryStructureAssignmentRead, status_code=status.HTTP_201_CREATED)
def create_salary_assignment(
    data: SalaryStructureAssignmentCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a salary structure assignment."""
    svc = PayrollService(db)
    assignment = svc.create_assignment(
        org_id=organization_id,
        employee_id=data.employee_id,
        structure_id=data.structure_id,
        from_date=data.from_date,
        to_date=data.to_date,
        base=data.base,
        variable=data.variable,
        income_tax_slab=data.income_tax_slab,
    )
    db.commit()
    return SalaryStructureAssignmentRead.model_validate(assignment)


@router.get("/assignments/{assignment_id}", response_model=SalaryStructureAssignmentRead)
def get_salary_assignment(
    assignment_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a salary structure assignment by ID."""
    svc = PayrollService(db)
    assignment = svc.get_assignment(organization_id, assignment_id)
    return SalaryStructureAssignmentRead.model_validate(assignment)


@router.patch("/assignments/{assignment_id}", response_model=SalaryStructureAssignmentRead)
def update_salary_assignment(
    assignment_id: UUID,
    data: SalaryStructureAssignmentUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a salary structure assignment."""
    svc = PayrollService(db)
    update_data = data.model_dump(exclude_unset=True)
    assignment = svc.update_assignment(organization_id, assignment_id, **update_data)
    db.commit()
    return SalaryStructureAssignmentRead.model_validate(assignment)


@router.delete("/assignments/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_salary_assignment(
    assignment_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete a salary structure assignment."""
    svc = PayrollService(db)
    svc.delete_assignment(organization_id, assignment_id)
    db.commit()


# =============================================================================
# Salary Slips
# =============================================================================


@router.get("/slips", response_model=SalarySlipListResponse)
def list_salary_slips(
    organization_id: UUID = Depends(require_organization_id),
    employee_id: Optional[UUID] = None,
    status: Optional[SalarySlipStatus] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List salary slips."""
    slips = salary_slip_service.list(
        db=db,
        organization_id=organization_id,
        employee_id=employee_id,
        status=status,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        offset=offset,
    )

    # Get total count
    org_id = coerce_uuid(organization_id)
    query = db.query(SalarySlip).filter(SalarySlip.organization_id == org_id)
    if employee_id:
        query = query.filter(SalarySlip.employee_id == employee_id)
    if status:
        query = query.filter(SalarySlip.status == status)
    total = query.count()

    return SalarySlipListResponse(
        items=[SalarySlipRead.model_validate(s) for s in slips],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/slips/export")
def export_salary_slips(
    organization_id: UUID = Depends(require_organization_id),
    employee_id: Optional[UUID] = None,
    status: Optional[SalarySlipStatus] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    db: Session = Depends(get_db),
):
    """Export salary slips to CSV."""
    slips = salary_slip_service.list(
        db=db,
        organization_id=organization_id,
        employee_id=employee_id,
        status=status,
        from_date=from_date,
        to_date=to_date,
        limit=10000,
        offset=0,
    )

    rows = [
        [
            "slip_id",
            "employee_id",
            "start_date",
            "end_date",
            "gross_pay",
            "total_deduction",
            "net_pay",
            "status",
        ]
    ]
    for slip in slips:
        rows.append(
            [
                str(slip.slip_id),
                str(slip.employee_id),
                slip.start_date.isoformat(),
                slip.end_date.isoformat(),
                str(slip.gross_pay),
                str(slip.total_deduction),
                str(slip.net_pay),
                slip.status.value,
            ]
        )
    return csv_response(rows, "salary_slips.csv")


@router.post("/slips", response_model=SalarySlipRead, status_code=status.HTTP_201_CREATED)
def create_salary_slip(
    data: SalarySlipCreate,
    user_id: UUID = Query(..., description="ID of user creating the slip"),
    db: Session = Depends(get_db),
):
    """Create a new salary slip and calculate amounts from structure."""
    input = SalarySlipInput(
        employee_id=data.employee_id,
        start_date=data.start_date,
        end_date=data.end_date,
        posting_date=data.posting_date,
        total_working_days=data.total_working_days,
        absent_days=data.absent_days,
        leave_without_pay=data.leave_without_pay,
    )

    slip = salary_slip_service.create_salary_slip(
        db=db,
        organization_id=data.organization_id,
        input=input,
        created_by_user_id=user_id,
    )

    return SalarySlipRead.model_validate(slip)


@router.get("/slips/{slip_id}", response_model=SalarySlipRead)
def get_salary_slip(
    slip_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a salary slip by ID."""
    slip = salary_slip_service.get(db, organization_id, slip_id)
    return SalarySlipRead.model_validate(slip)


@router.post("/slips/{slip_id}/submit", response_model=SalarySlipRead)
def submit_salary_slip(
    slip_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Submit a salary slip for approval."""
    slip = salary_slip_service.submit_salary_slip(
        db=db,
        organization_id=organization_id,
        slip_id=slip_id,
        submitted_by_user_id=user_id,
    )
    return SalarySlipRead.model_validate(slip)


@router.post("/slips/{slip_id}/approve", response_model=SalarySlipRead)
def approve_salary_slip(
    slip_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Approve a salary slip."""
    slip = salary_slip_service.approve_salary_slip(
        db=db,
        organization_id=organization_id,
        slip_id=slip_id,
        approved_by_user_id=user_id,
    )
    return SalarySlipRead.model_validate(slip)


@router.post("/slips/{slip_id}/post", response_model=SalarySlipPostResponse)
def post_salary_slip(
    slip_id: UUID,
    data: SalarySlipPostRequest,
    db: Session = Depends(get_db),
):
    """Post a salary slip to GL."""
    result = payroll_gl_adapter.post_salary_slip(
        db=db,
        organization_id=data.organization_id,
        slip_id=slip_id,
        posting_date=data.posting_date,
        posted_by_user_id=data.user_id,
    )

    return SalarySlipPostResponse(
        success=result.success,
        message=result.message,
        journal_entry_id=result.journal_entry_id,
    )


@router.post("/slips/{slip_id}/reverse", response_model=SalarySlipPostResponse)
def reverse_salary_slip(
    slip_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    user_id: UUID = Query(...),
    reversal_date: date = Query(...),
    reason: str = Query(...),
    db: Session = Depends(get_db),
):
    """Reverse a posted salary slip."""
    result = payroll_gl_adapter.reverse_salary_slip_posting(
        db=db,
        organization_id=organization_id,
        slip_id=slip_id,
        reversal_date=reversal_date,
        reversed_by_user_id=user_id,
        reason=reason,
    )

    return SalarySlipPostResponse(
        success=result.success,
        message=result.message,
        journal_entry_id=result.journal_entry_id,
    )


# =============================================================================
# Payroll Entries
# =============================================================================


@router.get("/entries", response_model=PayrollEntryListResponse)
def list_payroll_entries(
    organization_id: UUID = Depends(require_organization_id),
    status: Optional[PayrollEntryStatus] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    payroll_frequency: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List payroll entries."""
    svc = PayrollService(db)
    frequency = None
    if payroll_frequency:
        try:
            frequency = PayrollFrequency(payroll_frequency)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid payroll frequency") from exc
    result = svc.list_payroll_entries(
        org_id=organization_id,
        status=status,
        from_date=from_date,
        to_date=to_date,
        payroll_frequency=frequency,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return PayrollEntryListResponse(
        items=[PayrollEntryRead.model_validate(e) for e in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post("/entries", response_model=PayrollEntryRead, status_code=status.HTTP_201_CREATED)
def create_payroll_entry(
    data: PayrollEntryCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a payroll entry."""
    svc = PayrollService(db)
    entry = svc.create_payroll_entry(
        org_id=organization_id,
        posting_date=data.posting_date,
        start_date=data.start_date,
        end_date=data.end_date,
        payroll_frequency=data.payroll_frequency,
        currency_code=data.currency_code,
        department_id=data.department_id,
        designation_id=data.designation_id,
        notes=data.notes,
    )
    db.commit()
    return PayrollEntryRead.model_validate(entry)


@router.get("/entries/{entry_id}", response_model=PayrollEntryRead)
def get_payroll_entry(
    entry_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a payroll entry by ID."""
    svc = PayrollService(db)
    entry = svc.get_payroll_entry(organization_id, entry_id)
    return PayrollEntryRead.model_validate(entry)


@router.patch("/entries/{entry_id}", response_model=PayrollEntryRead)
def update_payroll_entry(
    entry_id: UUID,
    data: PayrollEntryUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a payroll entry."""
    svc = PayrollService(db)
    update_data = data.model_dump(exclude_unset=True)
    entry = svc.update_payroll_entry(organization_id, entry_id, **update_data)
    db.commit()
    return PayrollEntryRead.model_validate(entry)


@router.delete("/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_payroll_entry(
    entry_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete a payroll entry."""
    svc = PayrollService(db)
    svc.delete_payroll_entry(organization_id, entry_id)
    db.commit()


@router.post("/entries/{entry_id}/generate-slips", response_model=PayrollSlipGenerationResult)
def generate_salary_slips(
    entry_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
    auth: dict = Depends(require_tenant_auth),
):
    """Generate salary slips for a payroll entry."""
    svc = PayrollService(db)
    result = svc.generate_salary_slips(
        org_id=organization_id,
        entry_id=entry_id,
        created_by_id=UUID(auth["person_id"]),
    )
    db.commit()
    return PayrollSlipGenerationResult(**result)


@router.post("/entries/{entry_id}/regenerate-slips", response_model=PayrollSlipGenerationResult)
def regenerate_salary_slips(
    entry_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
    auth: dict = Depends(require_tenant_auth),
):
    """Regenerate salary slips for a payroll entry."""
    svc = PayrollService(db)
    result = svc.regenerate_salary_slips(
        org_id=organization_id,
        entry_id=entry_id,
        created_by_id=UUID(auth["person_id"]),
    )
    db.commit()
    return PayrollSlipGenerationResult(**result)


@router.post("/entries/{entry_id}/payouts", response_model=PayrollPayoutResult)
def payout_payroll_entry(
    entry_id: UUID,
    payload: PayrollPayoutRequest,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
    auth: dict = Depends(require_tenant_auth),
):
    """Mark payroll entry slips as paid."""
    svc = PayrollService(db)
    result = svc.payout_payroll_entry(
        org_id=organization_id,
        entry_id=entry_id,
        paid_by_id=UUID(auth["person_id"]),
        slip_ids=payload.slip_ids,
        payment_reference=payload.payment_reference,
    )
    db.commit()
    return PayrollPayoutResult(**result)


@router.post("/entries/{entry_id}/handoff")
def handoff_payroll_to_books(
    entry_id: UUID,
    posting_date: date = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
    auth: dict = Depends(require_tenant_auth),
):
    """Post payroll entry to GL."""
    svc = PayrollService(db)
    result = svc.handoff_payroll_to_books(
        org_id=organization_id,
        entry_id=entry_id,
        posting_date=posting_date,
        user_id=UUID(auth["person_id"]),
    )
    db.commit()
    return result


# =============================================================================
# PAYE Tax (NTA 2025)
# =============================================================================

from decimal import Decimal as PyDecimal
from pydantic import BaseModel, Field
from app.services.people.payroll.paye_calculator import PAYECalculator, PAYEBreakdown
from app.models.people.payroll.tax_band import TaxBand
from app.models.people.payroll.employee_tax_profile import EmployeeTaxProfile


class TaxBandRead(BaseModel):
    """Tax band response schema."""

    tax_band_id: UUID
    organization_id: UUID
    name: str
    min_amount: PyDecimal
    max_amount: Optional[PyDecimal]
    rate: PyDecimal
    effective_from: date
    effective_to: Optional[date]
    sequence: int
    is_active: bool

    model_config = {"from_attributes": True}


class TaxBandListResponse(BaseModel):
    """Tax band list response."""

    items: list[TaxBandRead]
    total: int


# Maximum reasonable salary values to prevent abuse/overflow (₦1 billion monthly)
MAX_MONTHLY_SALARY = PyDecimal("1000000000")
MAX_ANNUAL_RENT = PyDecimal("100000000")  # ₦100 million


class PAYECalculateRequest(BaseModel):
    """PAYE calculation request."""

    gross_monthly: PyDecimal = Field(
        ..., gt=0, le=MAX_MONTHLY_SALARY, description="Monthly gross salary (max ₦1B)"
    )
    basic_monthly: PyDecimal = Field(
        ..., gt=0, le=MAX_MONTHLY_SALARY, description="Monthly basic salary (max ₦1B)"
    )
    annual_rent: Optional[PyDecimal] = Field(
        None, ge=0, le=MAX_ANNUAL_RENT, description="Annual rent for relief (max ₦100M)"
    )
    rent_verified: bool = Field(False, description="Whether rent documentation is verified")
    pension_rate: Optional[PyDecimal] = Field(None, ge=0, le=1, description="Override pension rate")
    nhf_rate: Optional[PyDecimal] = Field(None, ge=0, le=1, description="Override NHF rate")
    nhis_rate: Optional[PyDecimal] = Field(None, ge=0, le=1, description="Override NHIS rate")
    employee_id: Optional[UUID] = Field(None, description="Employee ID for profile lookup")


class TaxBandBreakdownResponse(BaseModel):
    """Tax band breakdown in calculation response."""

    band_name: str
    range: str
    rate_percent: str
    taxable_in_band: str
    tax_amount: str


class PAYEBreakdownResponse(BaseModel):
    """
    PAYE calculation response with complete breakdown.

    All monetary amounts are returned as strings to preserve decimal precision.
    """

    # Input amounts (annual)
    annual_gross: str
    annual_basic: str
    annual_rent: str

    # Statutory deductions (annual)
    pension_amount: str
    pension_rate: str
    nhf_amount: str
    nhf_rate: str
    nhis_amount: str
    nhis_rate: str
    total_statutory: str

    # Rent relief
    rent_relief: str

    # Taxable income
    taxable_income: str

    # Tax calculation
    annual_tax: str
    monthly_tax: str
    monthly_pension: str
    monthly_nhf: str
    monthly_nhis: str

    # Effective rate
    effective_rate: str
    effective_rate_percent: str

    # Band breakdowns
    band_breakdowns: list[TaxBandBreakdownResponse]

    # Employee info
    employee_id: Optional[str] = None
    tin: Optional[str] = None
    tax_state: Optional[str] = None
    is_tax_exempt: bool = False


class EmployeeTaxProfileRead(BaseModel):
    """Employee tax profile response schema."""

    profile_id: UUID
    employee_id: UUID
    organization_id: UUID
    tin: Optional[str]
    tax_state: Optional[str]
    annual_rent: PyDecimal
    rent_receipt_verified: bool
    rent_relief_amount: Optional[PyDecimal]
    pension_rate: PyDecimal
    nhf_rate: PyDecimal
    nhis_rate: PyDecimal
    is_tax_exempt: bool
    exemption_reason: Optional[str]
    effective_from: date
    effective_to: Optional[date]

    model_config = {"from_attributes": True}


class EmployeeTaxProfileCreate(BaseModel):
    """Employee tax profile create request."""

    employee_id: UUID
    tin: Optional[str] = None
    tax_state: Optional[str] = None
    annual_rent: PyDecimal = Field(default=PyDecimal("0"))
    rent_receipt_verified: bool = False
    pension_rate: PyDecimal = Field(default=PyDecimal("0.08"))
    nhf_rate: PyDecimal = Field(default=PyDecimal("0.025"))
    nhis_rate: PyDecimal = Field(default=PyDecimal("0"))
    is_tax_exempt: bool = False
    exemption_reason: Optional[str] = None
    effective_from: date


class EmployeeTaxProfileUpdate(BaseModel):
    """Employee tax profile update request."""

    tin: Optional[str] = None
    tax_state: Optional[str] = None
    annual_rent: Optional[PyDecimal] = None
    rent_receipt_verified: Optional[bool] = None
    pension_rate: Optional[PyDecimal] = None
    nhf_rate: Optional[PyDecimal] = None
    nhis_rate: Optional[PyDecimal] = None
    is_tax_exempt: Optional[bool] = None
    exemption_reason: Optional[str] = None
    effective_to: Optional[date] = None


@router.get("/tax/bands", response_model=TaxBandListResponse)
def list_tax_bands(
    organization_id: UUID = Depends(require_organization_id),
    active_only: bool = Query(True, description="Only return active bands"),
    db: Session = Depends(get_db),
):
    """
    List tax bands for the organization.

    Returns NTA 2025 progressive tax bands:
    - Band 1: ₦0 - ₦800,000 @ 0%
    - Band 2: ₦800,001 - ₦3,000,000 @ 15%
    - Band 3: ₦3,000,001 - ₦12,000,000 @ 18%
    - Band 4: ₦12,000,001 - ₦25,000,000 @ 21%
    - Band 5: ₦25,000,001 - ₦50,000,000 @ 23%
    - Band 6: Above ₦50,000,000 @ 25%
    """
    calculator = PAYECalculator(db)
    bands = calculator.get_tax_bands(organization_id, active_only=active_only)

    return TaxBandListResponse(
        items=[TaxBandRead.model_validate(b) for b in bands],
        total=len(bands),
    )


@router.post("/tax/bands/seed-nta-2025", response_model=TaxBandListResponse)
def seed_nta_2025_tax_bands(
    organization_id: UUID = Depends(require_organization_id),
    effective_from: date = Query(default=date(2026, 1, 1), description="When bands become effective"),
    db: Session = Depends(get_db),
    auth: dict = Depends(require_tenant_auth),
):
    """
    Seed default NTA 2025 tax bands.

    Creates the standard Nigeria Tax Act 2025 progressive tax bands
    if they don't already exist for the organization.
    """
    calculator = PAYECalculator(db)
    created_bands = calculator.seed_nta_2025_bands(
        organization_id=organization_id,
        effective_from=effective_from,
        created_by_id=UUID(auth["person_id"]),
    )
    db.commit()

    if not created_bands:
        # Return existing bands
        bands = calculator.get_tax_bands(organization_id, active_only=True)
        return TaxBandListResponse(
            items=[TaxBandRead.model_validate(b) for b in bands],
            total=len(bands),
        )

    return TaxBandListResponse(
        items=[TaxBandRead.model_validate(b) for b in created_bands],
        total=len(created_bands),
    )


@router.post("/tax/calculate", response_model=PAYEBreakdownResponse)
def calculate_paye_tax(
    data: PAYECalculateRequest,
    organization_id: UUID = Depends(require_organization_id),
    as_of_date: date = Query(default=None, description="Date for tax band lookup"),
    db: Session = Depends(get_db),
) -> PAYEBreakdownResponse:
    """
    Calculate PAYE tax for given income.

    Implements NTA 2025 PAYE calculation:
    1. Annualize gross and basic salaries
    2. Calculate statutory deductions (Pension 8%, NHF 2.5%, NHIS)
    3. Calculate rent relief (20% of rent, max ₦500,000)
    4. Compute taxable income
    5. Apply progressive tax bands
    6. Prorate to monthly amount

    Returns a complete breakdown of the calculation.
    """
    calculator = PAYECalculator(db)
    breakdown = calculator.calculate(
        organization_id=organization_id,
        gross_monthly=data.gross_monthly,
        basic_monthly=data.basic_monthly,
        employee_id=data.employee_id,
        annual_rent=data.annual_rent,
        rent_verified=data.rent_verified,
        pension_rate=data.pension_rate,
        nhf_rate=data.nhf_rate,
        nhis_rate=data.nhis_rate,
        as_of_date=as_of_date,
    )

    # Convert to response model
    return PAYEBreakdownResponse(**breakdown.to_dict())


@router.get("/tax/profile/{employee_id}", response_model=EmployeeTaxProfileRead)
def get_employee_tax_profile(
    employee_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    as_of_date: date = Query(default=None, description="Date for profile lookup"),
    db: Session = Depends(get_db),
):
    """Get employee tax profile."""
    lookup_date = as_of_date or date.today()
    org_id = coerce_uuid(organization_id)

    profile = (
        db.query(EmployeeTaxProfile)
        .filter(
            EmployeeTaxProfile.organization_id == org_id,
            EmployeeTaxProfile.employee_id == employee_id,
            EmployeeTaxProfile.effective_from <= lookup_date,
            (
                (EmployeeTaxProfile.effective_to.is_(None))
                | (EmployeeTaxProfile.effective_to >= lookup_date)
            ),
        )
        .order_by(EmployeeTaxProfile.effective_from.desc())
        .first()
    )

    if not profile:
        raise HTTPException(status_code=404, detail="Employee tax profile not found")

    return EmployeeTaxProfileRead.model_validate(profile)


@router.post("/tax/profile", response_model=EmployeeTaxProfileRead, status_code=status.HTTP_201_CREATED)
def create_employee_tax_profile(
    data: EmployeeTaxProfileCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
    auth: dict = Depends(require_tenant_auth),
):
    """Create employee tax profile."""
    org_id = coerce_uuid(organization_id)

    profile = EmployeeTaxProfile(
        organization_id=org_id,
        employee_id=data.employee_id,
        tin=data.tin,
        tax_state=data.tax_state,
        annual_rent=data.annual_rent,
        rent_receipt_verified=data.rent_receipt_verified,
        pension_rate=data.pension_rate,
        nhf_rate=data.nhf_rate,
        nhis_rate=data.nhis_rate,
        is_tax_exempt=data.is_tax_exempt,
        exemption_reason=data.exemption_reason,
        effective_from=data.effective_from,
        created_by_id=UUID(auth["person_id"]),
    )

    # Calculate rent relief
    profile.update_rent_relief()

    db.add(profile)
    db.commit()
    db.refresh(profile)

    return EmployeeTaxProfileRead.model_validate(profile)


@router.patch("/tax/profile/{employee_id}", response_model=EmployeeTaxProfileRead)
def update_employee_tax_profile(
    employee_id: UUID,
    data: EmployeeTaxProfileUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
    auth: dict = Depends(require_tenant_auth),
):
    """Update employee tax profile."""
    org_id = coerce_uuid(organization_id)

    profile = (
        db.query(EmployeeTaxProfile)
        .filter(
            EmployeeTaxProfile.organization_id == org_id,
            EmployeeTaxProfile.employee_id == employee_id,
            EmployeeTaxProfile.effective_to.is_(None),
        )
        .order_by(EmployeeTaxProfile.effective_from.desc())
        .first()
    )

    if not profile:
        raise HTTPException(status_code=404, detail="Employee tax profile not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(profile, key, value)

    profile.updated_by_id = UUID(auth["person_id"])

    # Recalculate rent relief if rent changed
    if "annual_rent" in update_data or "rent_receipt_verified" in update_data:
        profile.update_rent_relief()

    db.commit()
    db.refresh(profile)

    return EmployeeTaxProfileRead.model_validate(profile)
