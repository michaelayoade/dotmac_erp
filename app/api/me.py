"""
Self-service API router for authenticated users.

Covers attendance (incl. geo check-in/out), leave (own + team approvals),
payslips, expenses (own claims, receipts, advances), the cross-module
approvals inbox, and notifications.
"""

from datetime import date, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db_with_org, require_tenant_auth
from app.models.people.exp import CashAdvanceStatus, ExpenseClaimStatus
from app.models.people.leave import LeaveApplicationStatus
from app.models.people.payroll.salary_slip import SalarySlipStatus
from app.models.people.perf.appraisal import AppraisalStatus
from app.schemas.people.attendance import (
    AttendanceListResponse,
    AttendanceRead,
)
from app.schemas.people.expense import (
    CashAdvanceRead,
    ExpenseClaimItemCreate,
    ExpenseClaimRead,
)
from app.schemas.people.leave import LeaveApplicationRead
from app.schemas.notification import NotificationListResponse, NotificationRead
from app.schemas.people.payroll import SalarySlipRead
from app.schemas.people.perf import AppraisalRead, ScorecardRead
from app.services.approvals_aggregator import (
    LEAVE_APPROVAL_PERMISSIONS,
    ApprovalsAggregatorService,
)
from app.services.common import PaginationParams
from app.services.expense.service_common import (
    ExpenseClaimStatusError,
    ExpenseLimitBlockedError,
    ExpenseNotFoundError,
    ExpenseServiceError,
)
from app.services.file_upload import FileUploadError, get_expense_receipt_upload
from app.services.notification import NotificationService
from app.services.push import PushService
from app.services.people.attendance import AttendanceService
from app.services.people.expense import ExpenseService
from app.services.people.hr.employees import EmployeeService
from app.services.people.leave import LeaveService
from app.services.people.payroll.salary_slip_service import salary_slip_service
from app.services.people.perf import PerformanceService
from app.services.people.training import TrainingService

router = APIRouter(
    prefix="/me",
    tags=["me"],
)


def _get_employee_id(db: Session, organization_id: UUID, person_id: UUID) -> UUID:
    svc = EmployeeService(db, organization_id)
    employee = svc.get_employee_by_person(person_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Employee record not found")
    return employee.employee_id


class MeCheckIn(BaseModel):
    """Self-service check-in payload (geo optional, captured at clock moment)."""

    check_in_time: datetime | None = None
    notes: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class MeCheckOut(BaseModel):
    """Self-service check-out payload (geo optional, captured at clock moment)."""

    check_out_time: datetime | None = None
    notes: str | None = None
    latitude: float | None = None
    longitude: float | None = None


def _get_direct_report_ids(
    db: Session, organization_id: UUID, manager_employee_id: UUID
) -> set[UUID]:
    """Position-based direct reports via OrgResolver (hr-hierarchy rule:
    never read Employee.reports_to_id for routing decisions)."""
    from app.services.people.hr.org_resolver import OrgResolver

    reports = OrgResolver(db).get_direct_reports(manager_employee_id, organization_id)
    return {emp.employee_id for emp in reports}


def _require_leave_approval_permission(auth: dict) -> None:
    roles = set(auth.get("roles") or [])
    scopes = set(auth.get("scopes") or [])
    if "admin" in roles or scopes.intersection(LEAVE_APPROVAL_PERMISSIONS):
        return
    raise HTTPException(status_code=403, detail="Leave approval permission required")


class LeaveApplicationRequest(BaseModel):
    """Request model for creating a leave application."""

    leave_type_id: UUID
    from_date: date
    to_date: date
    half_day: bool = False
    half_day_date: date | None = None
    reason: str | None = None


def _parse_month(month: str | None) -> tuple[date | None, date | None]:
    if not month:
        return None, None
    try:
        year, month_num = [int(part) for part in month.split("-", 1)]
        start_date = date(year, month_num, 1)
        if month_num == 12:
            end_date = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end_date = date(year, month_num + 1, 1) - timedelta(days=1)
        return start_date, end_date
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid month format") from exc


def _parse_status(value: str | None, enum_type, label: str):
    if not value:
        return None
    try:
        return enum_type(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {label}") from exc


# =============================================================================
# Leave
# =============================================================================


@router.get("/leave/balance")
def my_leave_balance(
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """Get leave balances for the current employee."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    employee_id = _get_employee_id(db, organization_id, person_id)
    balances = LeaveService(db).get_employee_balances(
        org_id=organization_id,
        employee_id=employee_id,
        as_of_date=date.today(),
    )
    return {"employee_id": employee_id, "balances": balances}


@router.get("/leave/applications")
def my_leave_applications(
    status: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """List leave applications for the current employee."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    employee_id = _get_employee_id(db, organization_id, person_id)
    status_value = None
    if status:
        try:
            status_value = LeaveApplicationStatus(status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid status") from exc
    result = LeaveService(db).list_applications(
        org_id=organization_id,
        employee_id=employee_id,
        status=status_value,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return {
        "items": [LeaveApplicationRead.model_validate(app) for app in result.items],
        "total": result.total,
        "offset": offset,
        "limit": limit,
    }


@router.post("/leave/applications", status_code=status.HTTP_201_CREATED)
def create_leave_application(
    payload: LeaveApplicationRequest,
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """Create a leave application for the current employee."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    employee_id = _get_employee_id(db, organization_id, person_id)
    application = LeaveService(db).create_application(
        org_id=organization_id,
        employee_id=employee_id,
        leave_type_id=payload.leave_type_id,
        from_date=payload.from_date,
        to_date=payload.to_date,
        half_day=payload.half_day,
        half_day_date=payload.half_day_date,
        reason=payload.reason,
    )
    return LeaveApplicationRead.model_validate(application)


@router.get("/leave/applications/{application_id}")
def get_leave_application(
    application_id: UUID,
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """Get a leave application for the current employee."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    employee_id = _get_employee_id(db, organization_id, person_id)
    application = LeaveService(db).get_application(organization_id, application_id)
    if application.employee_id != employee_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return LeaveApplicationRead.model_validate(application)


@router.post("/leave/applications/{application_id}/cancel")
def cancel_leave_application(
    application_id: UUID,
    reason: str | None = None,
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """Cancel a leave application for the current employee."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    employee_id = _get_employee_id(db, organization_id, person_id)
    application = LeaveService(db).get_application(organization_id, application_id)
    if application.employee_id != employee_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    application = LeaveService(db).cancel_application(
        org_id=organization_id,
        application_id=application_id,
        reason=reason,
    )
    return LeaveApplicationRead.model_validate(application)


@router.get("/team/leave-requests")
def team_leave_requests(
    status: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """List leave requests from direct reports."""
    _require_leave_approval_permission(auth)
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    manager_employee_id = _get_employee_id(db, organization_id, person_id)

    report_ids = list(_get_direct_report_ids(db, organization_id, manager_employee_id))
    if not report_ids:
        return {"items": [], "total": 0, "offset": offset, "limit": limit}

    status_value = None
    if status:
        try:
            status_value = LeaveApplicationStatus(status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid status") from exc

    result = LeaveService(db).list_team_applications(
        org_id=organization_id,
        employee_ids=report_ids,
        status=status_value,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    items = result.items
    total = result.total
    return {
        "items": [LeaveApplicationRead.model_validate(app) for app in items],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.post("/team/leave-requests/{application_id}/approve")
def approve_team_leave(
    application_id: UUID,
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """Approve a direct report leave request."""
    _require_leave_approval_permission(auth)
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    manager_employee_id = _get_employee_id(db, organization_id, person_id)

    application = LeaveService(db).get_application(organization_id, application_id)
    if application.employee_id == manager_employee_id:
        raise HTTPException(status_code=400, detail="Cannot approve own leave")

    report_ids = _get_direct_report_ids(db, organization_id, manager_employee_id)
    if application.employee_id not in report_ids:
        raise HTTPException(status_code=403, detail="Forbidden")

    application = LeaveService(db).approve_application(
        org_id=organization_id,
        application_id=application_id,
        approver_id=person_id,
    )
    return LeaveApplicationRead.model_validate(application)


@router.post("/team/leave-requests/{application_id}/reject")
def reject_team_leave(
    application_id: UUID,
    reason: str | None = None,
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """Reject a direct report leave request."""
    _require_leave_approval_permission(auth)
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    manager_employee_id = _get_employee_id(db, organization_id, person_id)

    application = LeaveService(db).get_application(organization_id, application_id)
    report_ids = _get_direct_report_ids(db, organization_id, manager_employee_id)
    if application.employee_id not in report_ids:
        raise HTTPException(status_code=403, detail="Forbidden")

    application = LeaveService(db).reject_application(
        org_id=organization_id,
        application_id=application_id,
        approver_id=person_id,
        reason=reason or "Rejected",
    )
    return LeaveApplicationRead.model_validate(application)


# =============================================================================
# Payslips
# =============================================================================


@router.get("/payslips")
def my_payslips(
    year: int | None = Query(None),
    status: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(12, ge=1, le=100),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """List salary slips for the current employee."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    employee_id = _get_employee_id(db, organization_id, person_id)

    status_value = None
    if status:
        try:
            status_value = SalarySlipStatus(status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid status") from exc

    from_date = None
    to_date = None
    if year:
        from_date = date(year, 1, 1)
        to_date = date(year, 12, 31)

    slips = salary_slip_service.list(
        db=db,
        organization_id=organization_id,
        employee_id=employee_id,
        status=status_value,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        offset=offset,
    )
    total_count = salary_slip_service.count(
        db=db,
        organization_id=organization_id,
        employee_id=employee_id,
        status=status_value,
        from_date=from_date,
        to_date=to_date,
    )

    return {
        "items": [SalarySlipRead.model_validate(s) for s in slips],
        "total": total_count,
        "offset": offset,
        "limit": limit,
    }


@router.get("/payslips/{slip_id}")
def my_payslip_detail(
    slip_id: UUID,
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """Get a salary slip for the current employee."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    employee_id = _get_employee_id(db, organization_id, person_id)

    slip = salary_slip_service.get(db, organization_id, slip_id)
    if slip.employee_id != employee_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return SalarySlipRead.model_validate(slip)


# =============================================================================
# Attendance
# =============================================================================


@router.get("/attendance", response_model=AttendanceListResponse)
def my_attendance(
    month: str | None = Query(None, description="Month in YYYY-MM format"),
    offset: int = Query(0, ge=0),
    limit: int = Query(31, ge=1, le=100),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """List attendance records for the current employee."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    employee_id = _get_employee_id(db, organization_id, person_id)
    from_date, to_date = _parse_month(month)

    svc = AttendanceService(db)
    result = svc.list_attendance(
        org_id=organization_id,
        employee_id=employee_id,
        from_date=from_date,
        to_date=to_date,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return AttendanceListResponse(
        items=[AttendanceRead.model_validate(a) for a in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.get("/attendance/today", response_model=AttendanceRead)
def my_attendance_today(
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """Get today's attendance record for the current employee."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    employee_id = _get_employee_id(db, organization_id, person_id)

    svc = AttendanceService(db)
    record = svc.get_attendance_by_date(
        organization_id, employee_id, svc.get_org_today(organization_id)
    )
    if not record:
        raise HTTPException(status_code=404, detail="Attendance record not found")
    return AttendanceRead.model_validate(record)


@router.post(
    "/attendance/check-in",
    response_model=AttendanceRead,
    status_code=status.HTTP_201_CREATED,
)
def my_check_in(
    payload: MeCheckIn,
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """Check in for the current employee."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    employee_id = _get_employee_id(db, organization_id, person_id)

    svc = AttendanceService(db)
    attendance = svc.check_in(
        org_id=organization_id,
        employee_id=employee_id,
        check_in_time=payload.check_in_time,
        notes=payload.notes,
        latitude=payload.latitude,
        longitude=payload.longitude,
    )
    return AttendanceRead.model_validate(attendance)


@router.post(
    "/attendance/check-out",
    response_model=AttendanceRead,
    status_code=status.HTTP_201_CREATED,
)
def my_check_out(
    payload: MeCheckOut,
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """Check out for the current employee."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    employee_id = _get_employee_id(db, organization_id, person_id)

    svc = AttendanceService(db)
    attendance = svc.check_out(
        org_id=organization_id,
        employee_id=employee_id,
        check_out_time=payload.check_out_time,
        notes=payload.notes,
        latitude=payload.latitude,
        longitude=payload.longitude,
    )
    return AttendanceRead.model_validate(attendance)


@router.get("/attendance/summary")
def my_attendance_summary(
    month: str | None = Query(None, description="Month in YYYY-MM format"),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """Get monthly attendance summary for the current employee."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    employee_id = _get_employee_id(db, organization_id, person_id)

    if month:
        try:
            year, month_num = [int(part) for part in month.split("-", 1)]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid month format") from exc
        return AttendanceService(db).get_employee_monthly_summary(
            org_id=organization_id,
            employee_id=employee_id,
            year=year,
            month=month_num,
        )

    svc = AttendanceService(db)
    today = svc.get_org_today(organization_id)
    return svc.get_employee_monthly_summary(
        org_id=organization_id,
        employee_id=employee_id,
        year=today.year,
        month=today.month,
    )


# =============================================================================
# Training
# =============================================================================


@router.get("/training/history")
def my_training_history(
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """Get training history for the current employee."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    employee_id = _get_employee_id(db, organization_id, person_id)
    return TrainingService(db).get_employee_training_history(
        org_id=organization_id,
        employee_id=employee_id,
    )


# =============================================================================
# Performance
# =============================================================================


@router.get("/performance/appraisals")
def my_appraisals(
    status: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """List appraisals for the current employee."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    employee_id = _get_employee_id(db, organization_id, person_id)
    status_value = _parse_status(status, AppraisalStatus, "status")
    result = PerformanceService(db).list_appraisals(
        org_id=organization_id,
        employee_id=employee_id,
        status=status_value,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return {
        "items": [AppraisalRead.model_validate(a) for a in result.items],
        "total": result.total,
        "offset": offset,
        "limit": limit,
    }


@router.get("/performance/scorecards")
def my_scorecards(
    is_finalized: bool | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """List scorecards for the current employee."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    employee_id = _get_employee_id(db, organization_id, person_id)
    result = PerformanceService(db).list_scorecards(
        org_id=organization_id,
        employee_id=employee_id,
        is_finalized=is_finalized,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return {
        "items": [ScorecardRead.model_validate(s) for s in result.items],
        "total": result.total,
        "offset": offset,
        "limit": limit,
    }


# =============================================================================
# Expenses
# =============================================================================


@router.get("/expenses/claims")
def my_expense_claims(
    status: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """List expense claims for the current employee."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    employee_id = _get_employee_id(db, organization_id, person_id)
    status_value = _parse_status(status, ExpenseClaimStatus, "status")
    result = ExpenseService(db).list_claims(
        org_id=organization_id,
        employee_id=employee_id,
        status=status_value,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return {
        "items": [ExpenseClaimRead.model_validate(c) for c in result.items],
        "total": result.total,
        "offset": offset,
        "limit": limit,
    }


@router.get("/expenses/advances")
def my_cash_advances(
    status: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """List cash advances for the current employee."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    employee_id = _get_employee_id(db, organization_id, person_id)
    status_value = _parse_status(status, CashAdvanceStatus, "status")
    result = ExpenseService(db).list_advances(
        org_id=organization_id,
        employee_id=employee_id,
        status=status_value,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return {
        "items": [CashAdvanceRead.model_validate(a) for a in result.items],
        "total": result.total,
        "offset": offset,
        "limit": limit,
    }


class MyExpenseClaimCreate(BaseModel):
    """Create-own-claim payload — employee is resolved from the token."""

    claim_date: date
    purpose: str
    currency_code: str | None = None
    notes: str | None = None
    items: list[ExpenseClaimItemCreate] = Field(default_factory=list)


@router.post("/expenses/claims", status_code=status.HTTP_201_CREATED)
def create_my_expense_claim(
    payload: MyExpenseClaimCreate,
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """Create an expense claim for the current employee (mobile self-service)."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    employee_id = _get_employee_id(db, organization_id, person_id)

    try:
        claim = ExpenseService(db).create_claim(
            org_id=organization_id,
            employee_id=employee_id,
            claim_date=payload.claim_date,
            purpose=payload.purpose,
            currency_code=payload.currency_code,
            notes=payload.notes,
            items=[item.model_dump() for item in payload.items],
            created_by_id=person_id,
        )
    except ExpenseNotFoundError:
        raise  # global handler maps these to 404
    except ExpenseServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ExpenseClaimRead.model_validate(claim)


@router.post("/expenses/claims/{claim_id}/submit")
def submit_my_expense_claim(
    claim_id: UUID,
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """Submit one of the current employee's own claims for approval."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    employee_id = _get_employee_id(db, organization_id, person_id)

    svc = ExpenseService(db)
    claim = svc.get_claim(organization_id, claim_id)
    if claim.employee_id != employee_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        result = svc.submit_claim(organization_id, claim_id, actor_id=person_id)
    except ExpenseClaimStatusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ExpenseLimitBlockedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ExpenseClaimRead.model_validate(result.claim)


@router.post("/expenses/claims/{claim_id}/items/{item_id}/receipt")
def upload_my_receipt(
    claim_id: UUID,
    item_id: UUID,
    receipt: UploadFile = File(...),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """Attach a receipt to one of my DRAFT claim items (appends, never
    replaces; rejected claims must be resubmitted to DRAFT first)."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    employee_id = _get_employee_id(db, organization_id, person_id)

    # Cheap early reject on the declared size before buffering the body;
    # FileUploadService.save() remains the authoritative validator.
    max_bytes = get_expense_receipt_upload().config.max_size_bytes
    if receipt.size is not None and receipt.size > max_bytes:
        raise HTTPException(status_code=400, detail="File too large")
    file_data = receipt.file.read()
    try:
        item = ExpenseService(db).attach_receipt(
            organization_id,
            claim_id=claim_id,
            item_id=item_id,
            employee_id=employee_id,
            file_data=file_data,
            content_type=receipt.content_type,
            original_filename=receipt.filename,
        )
    except FileUploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ExpenseClaimStatusError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"item_id": str(item.item_id), "receipt_url": item.receipt_url}


# =============================================================================
# Approvals inbox
# =============================================================================


@router.get("/approvals")
def my_pending_approvals(
    limit_per_type: int = Query(10, ge=1, le=50),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """Aggregate pending approvals across every category the caller may action."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])

    svc = ApprovalsAggregatorService(db)
    return svc.list_pending(
        organization_id=organization_id,
        person_id=person_id,
        roles=set(auth.get("roles") or []),
        scopes=set(auth.get("scopes") or []),
        limit_per_type=limit_per_type,
    )


# =============================================================================
# Notifications
# =============================================================================


@router.get("/notifications", response_model=NotificationListResponse)
def my_notifications(
    unread_only: bool = Query(False),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """List notifications for the current user (newest first)."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])

    svc = NotificationService()
    items = svc.list_notifications(
        db,
        person_id,
        organization_id=organization_id,
        unread_only=unread_only,
        offset=offset,
        limit=limit + 1,
    )
    has_more = len(items) > limit
    unread_count = svc.get_unread_count(db, person_id, organization_id=organization_id)
    return NotificationListResponse(
        items=[NotificationRead.model_validate(n) for n in items[:limit]],
        unread_count=unread_count,
        offset=offset,
        limit=limit,
        has_more=has_more,
    )


@router.post("/notifications/{notification_id}/read")
def mark_my_notification_read(
    notification_id: UUID,
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """Mark one of the current user's notifications as read."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])

    marked = NotificationService().mark_read(
        db,
        notification_id,
        recipient_id=person_id,
        organization_id=organization_id,
    )
    if not marked:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"marked_read": True}


@router.post("/notifications/mark-all-read")
def mark_all_my_notifications_read(
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """Mark all of the current user's notifications as read."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])

    count = NotificationService().mark_all_read(
        db,
        person_id,
        organization_id=organization_id,
    )
    return {"marked_read": count}


# =============================================================================
# Devices (mobile push registration — FF-1)
# =============================================================================


class DeviceRegisterRequest(BaseModel):
    """FCM device registration payload."""

    token: str = Field(min_length=8, max_length=512)
    platform: str = Field(pattern="^(android|ios|web)$")


class DeviceUnregisterRequest(BaseModel):
    """FCM device unregistration payload (on logout)."""

    token: str = Field(min_length=8, max_length=512)


@router.post("/devices", status_code=status.HTTP_201_CREATED)
def register_my_device(
    payload: DeviceRegisterRequest,
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """Register (or refresh) this device's push token for the current user."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])

    device = PushService(db).register_device(
        organization_id,
        person_id,
        token=payload.token,
        platform=payload.platform,
    )
    return {"device_token_id": str(device.device_token_id)}


@router.post("/devices/unregister")
def unregister_my_device(
    payload: DeviceUnregisterRequest,
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    """Stop push delivery to this device (idempotent; called on logout)."""
    person_id = UUID(auth["person_id"])

    revoked = PushService(db).unregister_device(person_id, token=payload.token)
    return {"revoked": revoked}
