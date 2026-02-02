"""
Self-service API router for authenticated users.

Currently implements attendance self-service endpoints.
"""
from datetime import date, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.api.deps import require_tenant_auth
from app.db import SessionLocal
from app.schemas.people.attendance import (
    AttendanceListResponse,
    AttendanceRead,
    AttendanceRecordCheckIn,
    AttendanceRecordCheckOut,
)
from app.schemas.people.leave import LeaveApplicationRead
from app.schemas.people.payroll import SalarySlipRead
from app.schemas.people.perf import AppraisalRead, ScorecardRead
from app.schemas.people.expense import ExpenseClaimRead, CashAdvanceRead
from app.models.people.payroll.salary_slip import SalarySlipStatus
from app.models.people.perf.appraisal import AppraisalStatus
from app.models.people.exp import ExpenseClaimStatus, CashAdvanceStatus
from app.services.people.payroll.salary_slip_service import salary_slip_service
from app.services.people.leave import LeaveService
from app.models.people.leave import LeaveApplicationStatus
from app.services.people.hr.employee_types import EmployeeFilters
from app.services.common import PaginationParams
from app.services.people.attendance import AttendanceService
from app.services.people.hr.employees import EmployeeService
from app.services.people.perf import PerformanceService
from app.services.people.training import TrainingService
from app.services.people.expense import ExpenseService

router = APIRouter(
    prefix="/me",
    tags=["me"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_employee_id(db: Session, organization_id: UUID, person_id: UUID) -> UUID:
    svc = EmployeeService(db, organization_id)
    employee = svc.get_employee_by_person(person_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Employee record not found")
    return employee.employee_id


LEAVE_APPROVAL_PERMISSIONS = {
    "leave:applications:approve:tier1",
    "leave:applications:approve:tier2",
    "leave:applications:approve:tier3",
}


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
    half_day_date: Optional[date] = None
    reason: Optional[str] = None


def _parse_month(month: Optional[str]) -> tuple[Optional[date], Optional[date]]:
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


def _parse_status(value: Optional[str], enum_type, label: str):
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
    db: Session = Depends(get_db),
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
    status: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db.commit()
    return LeaveApplicationRead.model_validate(application)


@router.get("/leave/applications/{application_id}")
def get_leave_application(
    application_id: UUID,
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
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
    reason: Optional[str] = None,
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
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
    db.commit()
    return LeaveApplicationRead.model_validate(application)


@router.get("/team/leave-requests")
def team_leave_requests(
    status: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
):
    """List leave requests from direct reports."""
    _require_leave_approval_permission(auth)
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    manager_employee_id = _get_employee_id(db, organization_id, person_id)

    employee_svc = EmployeeService(db, organization_id)
    reports = employee_svc.list_employees(
        filters=EmployeeFilters(reports_to_id=manager_employee_id),
        pagination=PaginationParams(offset=0, limit=1000),
    ).items
    report_ids = [emp.employee_id for emp in reports]
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
    db: Session = Depends(get_db),
):
    """Approve a direct report leave request."""
    _require_leave_approval_permission(auth)
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    manager_employee_id = _get_employee_id(db, organization_id, person_id)

    application = LeaveService(db).get_application(organization_id, application_id)
    if application.employee_id == manager_employee_id:
        raise HTTPException(status_code=400, detail="Cannot approve own leave")

    employee_svc = EmployeeService(db, organization_id)
    reports = employee_svc.list_employees(
        filters=EmployeeFilters(reports_to_id=manager_employee_id),
        pagination=PaginationParams(offset=0, limit=1000),
    ).items
    report_ids = {emp.employee_id for emp in reports}
    if application.employee_id not in report_ids:
        raise HTTPException(status_code=403, detail="Forbidden")

    application = LeaveService(db).approve_application(
        org_id=organization_id,
        application_id=application_id,
        approver_id=person_id,
    )
    db.commit()
    return LeaveApplicationRead.model_validate(application)


@router.post("/team/leave-requests/{application_id}/reject")
def reject_team_leave(
    application_id: UUID,
    reason: Optional[str] = None,
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
):
    """Reject a direct report leave request."""
    _require_leave_approval_permission(auth)
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    manager_employee_id = _get_employee_id(db, organization_id, person_id)

    application = LeaveService(db).get_application(organization_id, application_id)
    employee_svc = EmployeeService(db, organization_id)
    reports = employee_svc.list_employees(
        filters=EmployeeFilters(reports_to_id=manager_employee_id),
        pagination=PaginationParams(offset=0, limit=1000),
    ).items
    report_ids = {emp.employee_id for emp in reports}
    if application.employee_id not in report_ids:
        raise HTTPException(status_code=403, detail="Forbidden")

    application = LeaveService(db).reject_application(
        org_id=organization_id,
        application_id=application_id,
        approver_id=person_id,
        reason=reason or "Rejected",
    )
    db.commit()
    return LeaveApplicationRead.model_validate(application)


# =============================================================================
# Payslips
# =============================================================================


@router.get("/payslips")
def my_payslips(
    year: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(12, ge=1, le=100),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    month: Optional[str] = Query(None, description="Month in YYYY-MM format"),
    offset: int = Query(0, ge=0),
    limit: int = Query(31, ge=1, le=100),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
):
    """Get today's attendance record for the current employee."""
    organization_id = UUID(auth["organization_id"])
    person_id = UUID(auth["person_id"])
    employee_id = _get_employee_id(db, organization_id, person_id)

    svc = AttendanceService(db)
    record = svc.get_attendance_by_date(organization_id, employee_id, svc.get_org_today(organization_id))
    if not record:
        raise HTTPException(status_code=404, detail="Attendance record not found")
    return AttendanceRead.model_validate(record)


@router.post("/attendance/check-in", response_model=AttendanceRead, status_code=status.HTTP_201_CREATED)
def my_check_in(
    payload: AttendanceRecordCheckIn,
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
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
    )
    db.commit()
    return AttendanceRead.model_validate(attendance)


@router.post("/attendance/check-out", response_model=AttendanceRead, status_code=status.HTTP_201_CREATED)
def my_check_out(
    payload: AttendanceRecordCheckOut,
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
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
    )
    db.commit()
    return AttendanceRead.model_validate(attendance)


@router.get("/attendance/summary")
def my_attendance_summary(
    month: Optional[str] = Query(None, description="Month in YYYY-MM format"),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    status: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
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
    is_finalized: Optional[bool] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
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
    status: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
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
    status: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
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
