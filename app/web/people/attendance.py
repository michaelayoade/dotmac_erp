"""
Attendance management web routes.

Attendance list and shift type configuration pages.
"""
from datetime import date, datetime, time
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.people.attendance import AttendanceStatus
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.attendance import AttendanceService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context, get_db, require_hr_access


router = APIRouter(prefix="/attendance", tags=["people-attendance-web"])


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_uuid(value: Optional[str]) -> Optional[UUID]:
    if not value:
        return None
    try:
        return coerce_uuid(value)
    except Exception:
        return None


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
@router.get("/records", response_class=HTMLResponse)
def attendance_overview(
    request: Request,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    employee_id: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Attendance records list page."""
    org_id = coerce_uuid(auth.organization_id)
    pagination = PaginationParams.from_page(page, per_page=20)
    svc = AttendanceService(db)

    status_enum = None
    if status:
        try:
            status_enum = AttendanceStatus(status)
        except ValueError:
            status_enum = None

    result = svc.list_attendance(
        org_id,
        employee_id=_parse_uuid(employee_id),
        from_date=_parse_date(start_date),
        to_date=_parse_date(end_date),
        status=status_enum,
        pagination=pagination,
    )

    records = []
    for record in result.items:
        employee = record.employee
        shift_type = record.shift_type
        records.append(
            {
                "attendance_id": str(record.attendance_id),
                "attendance_date": record.attendance_date,
                "employee_name": employee.full_name if employee else "-",
                "employee_code": employee.employee_code if employee else "-",
                "status": record.status.value,
                "check_in": record.check_in,
                "check_out": record.check_out,
                "working_hours": record.working_hours,
                "shift_name": shift_type.shift_name if shift_type else "-",
                "late_entry": record.late_entry,
                "early_exit": record.early_exit,
            }
        )

    context = base_context(request, auth, "Attendance", "attendance", db=db)
    context["request"] = request
    context.update(
        {
            "records": records,
            "statuses": [s.value for s in AttendanceStatus],
            "status": status,
            "start_date": start_date,
            "end_date": end_date,
            "employee_id": employee_id,
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }
    )
    return templates.TemplateResponse(request, "people/attendance/records.html", context)


@router.post("/records/{attendance_id}/delete")
def delete_attendance_record(
    attendance_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Delete an attendance record."""
    org_id = coerce_uuid(auth.organization_id)
    svc = AttendanceService(db)
    try:
        svc.delete_attendance(org_id, coerce_uuid(attendance_id))
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url="/people/attendance", status_code=303)


@router.get("/shifts", response_class=HTMLResponse)
def attendance_shifts(
    request: Request,
    search: Optional[str] = None,
    is_active: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Shift type list page."""
    org_id = coerce_uuid(auth.organization_id)
    pagination = PaginationParams.from_page(page, per_page=20)
    svc = AttendanceService(db)

    active_filter = None
    if is_active == "true":
        active_filter = True
    elif is_active == "false":
        active_filter = False

    result = svc.list_shift_types(
        org_id,
        search=search,
        is_active=active_filter,
        pagination=pagination,
    )

    shifts = []
    for shift in result.items:
        shifts.append(
            {
                "shift_type_id": str(shift.shift_type_id),
                "shift_code": shift.shift_code,
                "shift_name": shift.shift_name,
                "start_time": shift.start_time,
                "end_time": shift.end_time,
                "working_hours": shift.working_hours,
                "is_active": shift.is_active,
            }
        )

    context = base_context(request, auth, "Shift Types", "attendance", db=db)
    context["request"] = request
    context.update(
        {
            "shifts": shifts,
            "search": search,
            "is_active": is_active,
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }
    )
    return templates.TemplateResponse(request, "people/attendance/shifts.html", context)


@router.get("/records/new", response_class=HTMLResponse)
def new_attendance_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New attendance record form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = AttendanceService(db)
    shifts = svc.list_shift_types(
        org_id,
        is_active=True,
        pagination=PaginationParams(offset=0, limit=200),
    ).items
    employees = (
        db.query(Employee)
        .filter(
            Employee.organization_id == org_id,
            Employee.status == EmployeeStatus.ACTIVE,
        )
        .order_by(Employee.employee_code)
        .all()
    )

    context = base_context(request, auth, "New Attendance", "attendance", db=db)
    context["request"] = request
    context["form_data"] = {}
    context["statuses"] = [s.value for s in AttendanceStatus]
    context["employees"] = employees
    context["shifts"] = shifts
    return templates.TemplateResponse(request, "people/attendance/record_form.html", context)


@router.post("/records/new", response_class=HTMLResponse)
async def create_attendance(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new attendance record."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    employee_id = (form.get("employee_id") or "").strip()
    attendance_date = (form.get("attendance_date") or "").strip()
    status = (form.get("status") or "").strip()
    shift_type_id = (form.get("shift_type_id") or "").strip()
    check_in = (form.get("check_in") or "").strip()
    check_out = (form.get("check_out") or "").strip()
    remarks = (form.get("remarks") or "").strip()

    form_data = {
        "employee_id": employee_id,
        "attendance_date": attendance_date,
        "status": status,
        "shift_type_id": shift_type_id,
        "check_in": check_in,
        "check_out": check_out,
        "remarks": remarks,
    }

    if not employee_id or not attendance_date or not status:
        org_id = coerce_uuid(auth.organization_id)
        svc = AttendanceService(db)
        shifts = svc.list_shift_types(
            org_id,
            is_active=True,
            pagination=PaginationParams(offset=0, limit=200),
        ).items
        employees = (
            db.query(Employee)
            .filter(
                Employee.organization_id == org_id,
                Employee.status == EmployeeStatus.ACTIVE,
            )
            .order_by(Employee.employee_code)
            .all()
        )
        context = base_context(request, auth, "New Attendance", "attendance", db=db)
        context["request"] = request
        context["form_data"] = form_data
        context["statuses"] = [s.value for s in AttendanceStatus]
        context["employees"] = employees
        context["shifts"] = shifts
        context["error"] = "Employee, date, and status are required."
        return templates.TemplateResponse(request, "people/attendance/record_form.html", context)

    try:
        org_id = coerce_uuid(auth.organization_id)
        svc = AttendanceService(db)
        svc.create_attendance(
            org_id,
            employee_id=coerce_uuid(employee_id),
            attendance_date=date.fromisoformat(attendance_date),
            status=AttendanceStatus(status),
            shift_type_id=coerce_uuid(shift_type_id) if shift_type_id else None,
            check_in=datetime.fromisoformat(check_in) if check_in else None,
            check_out=datetime.fromisoformat(check_out) if check_out else None,
            remarks=remarks or None,
        )
        db.commit()
        return RedirectResponse(url="/people/attendance", status_code=303)
    except Exception as exc:
        db.rollback()
        org_id = coerce_uuid(auth.organization_id)
        svc = AttendanceService(db)
        shifts = svc.list_shift_types(
            org_id,
            is_active=True,
            pagination=PaginationParams(offset=0, limit=200),
        ).items
        employees = (
            db.query(Employee)
            .filter(
                Employee.organization_id == org_id,
                Employee.status == EmployeeStatus.ACTIVE,
            )
            .order_by(Employee.employee_code)
            .all()
        )
        context = base_context(request, auth, "New Attendance", "attendance", db=db)
        context["request"] = request
        context["form_data"] = form_data
        context["statuses"] = [s.value for s in AttendanceStatus]
        context["employees"] = employees
        context["shifts"] = shifts
        context["error"] = str(exc)
        return templates.TemplateResponse(request, "people/attendance/record_form.html", context)


def _parse_decimal(value: Optional[str]) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        return Decimal(value)
    except Exception:
        return None


def _parse_int(value: Optional[str], default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _parse_time(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def _parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "on", "yes"}


def _shift_form_context(shift_type: Optional[dict] = None) -> dict:
    if not shift_type:
        return {}
    return {
        "shift_code": shift_type.get("shift_code", ""),
        "shift_name": shift_type.get("shift_name", ""),
        "start_time": shift_type.get("start_time"),
        "end_time": shift_type.get("end_time"),
        "working_hours": shift_type.get("working_hours"),
        "description": shift_type.get("description") or "",
        "late_entry_grace_period": shift_type.get("late_entry_grace_period", 0),
        "early_exit_grace_period": shift_type.get("early_exit_grace_period", 0),
        "enable_half_day": shift_type.get("enable_half_day", True),
        "half_day_threshold_hours": shift_type.get("half_day_threshold_hours"),
        "enable_overtime": shift_type.get("enable_overtime", False),
        "overtime_threshold_hours": shift_type.get("overtime_threshold_hours"),
        "break_duration_minutes": shift_type.get("break_duration_minutes", 60),
        "is_active": shift_type.get("is_active", True),
    }


@router.get("/shifts/new", response_class=HTMLResponse)
def new_shift_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New shift type form."""
    context = base_context(request, auth, "New Shift Type", "attendance", db=db)
    context["request"] = request
    context["form_data"] = {}
    context["form_action"] = "/people/attendance/shifts/new"
    context["is_edit"] = False
    return templates.TemplateResponse(request, "people/attendance/shift_form.html", context)


@router.post("/shifts/new", response_class=HTMLResponse)
async def create_shift(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new shift type."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()
    shift_code = (form.get("shift_code") or "").strip()
    shift_name = (form.get("shift_name") or "").strip()
    start_time = (form.get("start_time") or "").strip()
    end_time = (form.get("end_time") or "").strip()
    working_hours = (form.get("working_hours") or "").strip()
    description = (form.get("description") or "").strip()
    late_entry_grace_period = (form.get("late_entry_grace_period") or "").strip()
    early_exit_grace_period = (form.get("early_exit_grace_period") or "").strip()
    enable_half_day = form.get("enable_half_day")
    half_day_threshold_hours = (form.get("half_day_threshold_hours") or "").strip()
    enable_overtime = form.get("enable_overtime")
    overtime_threshold_hours = (form.get("overtime_threshold_hours") or "").strip()
    break_duration_minutes = (form.get("break_duration_minutes") or "").strip()
    is_active = form.get("is_active")

    svc = AttendanceService(db)
    org_id = coerce_uuid(auth.organization_id)

    form_data = {
        "shift_code": shift_code,
        "shift_name": shift_name,
        "start_time": start_time,
        "end_time": end_time,
        "working_hours": working_hours,
        "description": description,
        "late_entry_grace_period": late_entry_grace_period,
        "early_exit_grace_period": early_exit_grace_period,
        "enable_half_day": enable_half_day,
        "half_day_threshold_hours": half_day_threshold_hours,
        "enable_overtime": enable_overtime,
        "overtime_threshold_hours": overtime_threshold_hours,
        "break_duration_minutes": break_duration_minutes,
        "is_active": is_active,
    }

    if not shift_code or not shift_name or not start_time or not end_time:
        context = base_context(request, auth, "New Shift Type", "attendance", db=db)
        context["request"] = request
        context["form_data"] = form_data
        context["form_action"] = "/people/attendance/shifts/new"
        context["is_edit"] = False
        context["error"] = "Shift code, shift name, start time, and end time are required."
        return templates.TemplateResponse(request, "people/attendance/shift_form.html", context)

    try:
        svc.create_shift_type(
            org_id,
            shift_code=shift_code,
            shift_name=shift_name,
            start_time=_parse_time(start_time),
            end_time=_parse_time(end_time),
            working_hours=_parse_decimal(working_hours),
            description=description or None,
            late_entry_grace_period=_parse_int(late_entry_grace_period, 0),
            early_exit_grace_period=_parse_int(early_exit_grace_period, 0),
            enable_half_day=_parse_bool(enable_half_day, False),
            half_day_threshold_hours=_parse_decimal(half_day_threshold_hours),
            enable_overtime=_parse_bool(enable_overtime, False),
            overtime_threshold_hours=_parse_decimal(overtime_threshold_hours),
            break_duration_minutes=_parse_int(break_duration_minutes, 60),
            is_active=_parse_bool(is_active, False),
        )
        db.commit()
        return RedirectResponse(url="/people/attendance/shifts", status_code=303)
    except Exception as exc:
        db.rollback()
        context = base_context(request, auth, "New Shift Type", "attendance", db=db)
        context["request"] = request
        context["form_data"] = form_data
        context["form_action"] = "/people/attendance/shifts/new"
        context["is_edit"] = False
        context["error"] = str(exc)
        return templates.TemplateResponse(request, "people/attendance/shift_form.html", context)


@router.get("/shifts/{shift_type_id}/edit", response_class=HTMLResponse)
def edit_shift_form(
    request: Request,
    shift_type_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit shift type form."""
    svc = AttendanceService(db)
    org_id = coerce_uuid(auth.organization_id)
    shift = svc.get_shift_type(org_id, coerce_uuid(shift_type_id))
    context = base_context(request, auth, "Edit Shift Type", "attendance", db=db)
    context["request"] = request
    context["form_data"] = _shift_form_context(
        {
            "shift_code": shift.shift_code,
            "shift_name": shift.shift_name,
            "start_time": shift.start_time.strftime("%H:%M"),
            "end_time": shift.end_time.strftime("%H:%M"),
            "working_hours": shift.working_hours,
            "description": shift.description,
            "late_entry_grace_period": shift.late_entry_grace_period,
            "early_exit_grace_period": shift.early_exit_grace_period,
            "enable_half_day": shift.enable_half_day,
            "half_day_threshold_hours": shift.half_day_threshold_hours,
            "enable_overtime": shift.enable_overtime,
            "overtime_threshold_hours": shift.overtime_threshold_hours,
            "break_duration_minutes": shift.break_duration_minutes,
            "is_active": shift.is_active,
        }
    )
    context["form_action"] = f"/people/attendance/shifts/{shift_type_id}/edit"
    context["is_edit"] = True
    return templates.TemplateResponse(request, "people/attendance/shift_form.html", context)


@router.post("/shifts/{shift_type_id}/edit", response_class=HTMLResponse)
async def update_shift(
    request: Request,
    shift_type_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update a shift type."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()
    shift_code = (form.get("shift_code") or "").strip()
    shift_name = (form.get("shift_name") or "").strip()
    start_time = (form.get("start_time") or "").strip()
    end_time = (form.get("end_time") or "").strip()
    working_hours = (form.get("working_hours") or "").strip()
    description = (form.get("description") or "").strip()
    late_entry_grace_period = (form.get("late_entry_grace_period") or "").strip()
    early_exit_grace_period = (form.get("early_exit_grace_period") or "").strip()
    enable_half_day = form.get("enable_half_day")
    half_day_threshold_hours = (form.get("half_day_threshold_hours") or "").strip()
    enable_overtime = form.get("enable_overtime")
    overtime_threshold_hours = (form.get("overtime_threshold_hours") or "").strip()
    break_duration_minutes = (form.get("break_duration_minutes") or "").strip()
    is_active = form.get("is_active")

    form_data = {
        "shift_code": shift_code,
        "shift_name": shift_name,
        "start_time": start_time,
        "end_time": end_time,
        "working_hours": working_hours,
        "description": description,
        "late_entry_grace_period": late_entry_grace_period,
        "early_exit_grace_period": early_exit_grace_period,
        "enable_half_day": enable_half_day,
        "half_day_threshold_hours": half_day_threshold_hours,
        "enable_overtime": enable_overtime,
        "overtime_threshold_hours": overtime_threshold_hours,
        "break_duration_minutes": break_duration_minutes,
        "is_active": is_active,
    }

    if not shift_code or not shift_name or not start_time or not end_time:
        context = base_context(request, auth, "Edit Shift Type", "attendance", db=db)
        context["request"] = request
        context["form_data"] = form_data
        context["form_action"] = f"/people/attendance/shifts/{shift_type_id}/edit"
        context["is_edit"] = True
        context["error"] = "Shift code, shift name, start time, and end time are required."
        return templates.TemplateResponse(request, "people/attendance/shift_form.html", context)

    try:
        svc = AttendanceService(db)
        org_id = coerce_uuid(auth.organization_id)
        svc.update_shift_type(
            org_id,
            coerce_uuid(shift_type_id),
            shift_code=shift_code,
            shift_name=shift_name,
            start_time=_parse_time(start_time),
            end_time=_parse_time(end_time),
            working_hours=_parse_decimal(working_hours),
            description=description or None,
            late_entry_grace_period=_parse_int(late_entry_grace_period, 0),
            early_exit_grace_period=_parse_int(early_exit_grace_period, 0),
            enable_half_day=_parse_bool(enable_half_day, False),
            half_day_threshold_hours=_parse_decimal(half_day_threshold_hours),
            enable_overtime=_parse_bool(enable_overtime, False),
            overtime_threshold_hours=_parse_decimal(overtime_threshold_hours),
            break_duration_minutes=_parse_int(break_duration_minutes, 60),
            is_active=_parse_bool(is_active, False),
        )
        db.commit()
        return RedirectResponse(url="/people/attendance/shifts", status_code=303)
    except Exception as exc:
        db.rollback()
        context = base_context(request, auth, "Edit Shift Type", "attendance", db=db)
        context["request"] = request
        context["form_data"] = form_data
        context["form_action"] = f"/people/attendance/shifts/{shift_type_id}/edit"
        context["is_edit"] = True
        context["error"] = str(exc)
        return templates.TemplateResponse(request, "people/attendance/shift_form.html", context)


# =============================================================================
# Reports
# =============================================================================


@router.get("/reports/summary", response_class=HTMLResponse)
def attendance_summary_report(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    department_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Attendance summary report page."""
    from app.services.people.hr import OrganizationService, DepartmentFilters

    org_id = coerce_uuid(auth.organization_id)
    svc = AttendanceService(db)
    org_svc = OrganizationService(db, org_id)

    report = svc.get_attendance_summary_report(
        org_id,
        start_date=_parse_date(start_date),
        end_date=_parse_date(end_date),
        department_id=_parse_uuid(department_id),
    )

    departments = org_svc.list_departments(
        DepartmentFilters(is_active=True),
        PaginationParams(limit=200),
    ).items

    context = base_context(request, auth, "Attendance Summary Report", "attendance", db=db)
    context.update({
        "report": report,
        "departments": departments,
        "start_date": start_date or report["start_date"].isoformat(),
        "end_date": end_date or report["end_date"].isoformat(),
        "department_id": department_id,
    })
    return templates.TemplateResponse(request, "people/attendance/reports/summary.html", context)


@router.get("/reports/by-employee", response_class=HTMLResponse)
def attendance_by_employee_report(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    department_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Attendance by employee report page."""
    from app.services.people.hr import OrganizationService, DepartmentFilters

    org_id = coerce_uuid(auth.organization_id)
    svc = AttendanceService(db)
    org_svc = OrganizationService(db, org_id)

    report = svc.get_attendance_by_employee_report(
        org_id,
        start_date=_parse_date(start_date),
        end_date=_parse_date(end_date),
        department_id=_parse_uuid(department_id),
    )

    departments = org_svc.list_departments(
        DepartmentFilters(is_active=True),
        PaginationParams(limit=200),
    ).items

    context = base_context(request, auth, "Attendance by Employee", "attendance", db=db)
    context.update({
        "report": report,
        "departments": departments,
        "start_date": start_date or report["start_date"].isoformat(),
        "end_date": end_date or report["end_date"].isoformat(),
        "department_id": department_id,
    })
    return templates.TemplateResponse(request, "people/attendance/reports/by_employee.html", context)


@router.get("/reports/late-early", response_class=HTMLResponse)
def attendance_late_early_report(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    department_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Late arrivals and early departures report page."""
    from app.services.people.hr import OrganizationService, DepartmentFilters

    org_id = coerce_uuid(auth.organization_id)
    svc = AttendanceService(db)
    org_svc = OrganizationService(db, org_id)

    report = svc.get_late_early_report(
        org_id,
        start_date=_parse_date(start_date),
        end_date=_parse_date(end_date),
        department_id=_parse_uuid(department_id),
    )

    departments = org_svc.list_departments(
        DepartmentFilters(is_active=True),
        PaginationParams(limit=200),
    ).items

    context = base_context(request, auth, "Late/Early Report", "attendance", db=db)
    context.update({
        "report": report,
        "departments": departments,
        "start_date": start_date or report["start_date"].isoformat(),
        "end_date": end_date or report["end_date"].isoformat(),
        "department_id": department_id,
    })
    return templates.TemplateResponse(request, "people/attendance/reports/late_early.html", context)


@router.get("/reports/trends", response_class=HTMLResponse)
def attendance_trends_report(
    request: Request,
    months: int = Query(default=12, ge=3, le=24),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Attendance trends report page."""
    org_id = coerce_uuid(auth.organization_id)
    svc = AttendanceService(db)

    report = svc.get_attendance_trends_report(org_id, months=months)

    context = base_context(request, auth, "Attendance Trends Report", "attendance", db=db)
    context.update({
        "report": report,
        "months": months,
    })
    return templates.TemplateResponse(request, "people/attendance/reports/trends.html", context)
