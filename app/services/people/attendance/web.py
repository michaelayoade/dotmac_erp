"""
Attendance web view service.

Provides view-focused data and operations for attendance web routes.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Optional, cast
from uuid import UUID
from urllib.parse import quote

from fastapi import Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.people.attendance import AttendanceStatus
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.attendance import AttendanceService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context


class AttendanceWebService:
    """Web service methods for attendance pages."""

    @staticmethod
    def _parse_date(value: Optional[str]) -> Optional[date]:
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _parse_uuid(value: Optional[str]) -> Optional[UUID]:
        if not value:
            return None
        try:
            return cast(UUID, coerce_uuid(value))
        except Exception:
            return None

    @staticmethod
    def _parse_decimal(value: Optional[str]) -> Optional[Decimal]:
        if value is None or value == "":
            return None
        try:
            return Decimal(str(value))
        except Exception:
            return None

    @staticmethod
    def _parse_int(value: Optional[str], default: int = 0) -> int:
        if value is None or value == "":
            return default
        try:
            return int(str(value))
        except ValueError:
            return default

    @staticmethod
    def _parse_time(value: str) -> time:
        return datetime.strptime(value, "%H:%M").time()

    @staticmethod
    def _parse_bool(value: Optional[str], default: bool = False) -> bool:
        if value is None:
            return default
        return value.lower() in {"1", "true", "on", "yes"}

    @staticmethod
    def _get_form_str(form: Any, key: str, default: str = "") -> str:
        value = form.get(key, default) if form is not None else default
        if isinstance(value, UploadFile) or value is None:
            return default
        return str(value).strip()

    @staticmethod
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

    @staticmethod
    def _get_employees(db: Session, org_id: UUID) -> list[Employee]:
        """Get active employees for dropdowns."""
        from sqlalchemy import select
        from app.models.person import Person

        stmt = (
            select(Employee)
            .join(Person, Employee.person_id == Person.id)
            .where(Employee.organization_id == org_id)
            .where(Employee.status == EmployeeStatus.ACTIVE)
            .order_by(Person.first_name.asc(), Person.last_name.asc())
        )
        return list(db.scalars(stmt).all())

    @staticmethod
    def attendance_overview_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        employee_id: Optional[str],
        page: int,
        success: Optional[str],
        error: Optional[str],
    ) -> HTMLResponse:
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
            employee_id=AttendanceWebService._parse_uuid(employee_id),
            from_date=AttendanceWebService._parse_date(start_date),
            to_date=AttendanceWebService._parse_date(end_date),
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
                    "overtime_hours": record.overtime_hours,
                    "shift_name": shift_type.shift_name if shift_type else "-",
                    "late_entry": record.late_entry,
                    "early_exit": record.early_exit,
                }
            )

        employees = AttendanceWebService._get_employees(db, org_id)
        shifts = svc.list_shift_types(org_id, is_active=True).items

        context = base_context(request, auth, "Attendance", "attendance", db=db)
        context["request"] = request
        context.update(
            {
                "records": records,
                "employees": employees,
                "shifts": shifts,
                "today": date.today().isoformat(),
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
                "success": success,
                "error": error,
            }
        )
        return templates.TemplateResponse(request, "people/attendance/records.html", context)

    @staticmethod
    def delete_attendance_record_response(
        auth: WebAuthContext,
        db: Session,
        attendance_id: str,
    ) -> RedirectResponse:
        """Delete an attendance record."""
        org_id = coerce_uuid(auth.organization_id)
        svc = AttendanceService(db)
        svc.delete_attendance(org_id, coerce_uuid(attendance_id))
        db.commit()
        return RedirectResponse(url="/people/attendance", status_code=303)

    @staticmethod
    async def bulk_mark_attendance_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Bulk mark attendance for multiple employees."""
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        employee_ids = form.getlist("employee_ids")
        attendance_date_str = AttendanceWebService._get_form_str(form, "attendance_date")
        status_str = AttendanceWebService._get_form_str(form, "status")
        shift_type_id = AttendanceWebService._get_form_str(form, "shift_type_id")

        if not employee_ids:
            return RedirectResponse(
                url="/people/attendance?error=No+employees+selected",
                status_code=303
            )

        if not attendance_date_str or not status_str:
            return RedirectResponse(
                url="/people/attendance?error=Date+and+status+are+required",
                status_code=303
            )

        valid_ids = []
        for emp_id in employee_ids:
            try:
                valid_ids.append(coerce_uuid(emp_id))
            except Exception:
                pass

        if not valid_ids:
            return RedirectResponse(
                url="/people/attendance?error=No+valid+employees+selected",
                status_code=303
            )

        try:
            org_id = coerce_uuid(auth.organization_id)
            svc = AttendanceService(db)

            result = svc.bulk_mark_attendance(
                org_id,
                employee_ids=valid_ids,
                attendance_date=date.fromisoformat(attendance_date_str),
                status=AttendanceStatus(status_str),
                shift_type_id=coerce_uuid(shift_type_id) if shift_type_id else None,
            )
            db.commit()

            success_msg = quote(
                f"Marked attendance for {result['success_count']} employee(s). {result['failed_count']} failed."
            )
            return RedirectResponse(url=f"/people/attendance?success={success_msg}", status_code=303)
        except Exception as e:
            db.rollback()
            error_msg = quote(str(e))
            return RedirectResponse(url=f"/people/attendance?error={error_msg}", status_code=303)

    @staticmethod
    def attendance_shifts_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: Optional[str],
        is_active: Optional[str],
        page: int,
    ) -> HTMLResponse:
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

    @staticmethod
    def new_attendance_form_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
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

    @staticmethod
    async def create_attendance_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Create a new attendance record."""
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        employee_id = AttendanceWebService._get_form_str(form, "employee_id")
        attendance_date = AttendanceWebService._get_form_str(form, "attendance_date")
        status = AttendanceWebService._get_form_str(form, "status")
        shift_type_id = AttendanceWebService._get_form_str(form, "shift_type_id")
        check_in = AttendanceWebService._get_form_str(form, "check_in")
        check_out = AttendanceWebService._get_form_str(form, "check_out")
        remarks = AttendanceWebService._get_form_str(form, "remarks")

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

    @staticmethod
    def new_shift_form_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """New shift type form."""
        context = base_context(request, auth, "New Shift Type", "attendance", db=db)
        context["request"] = request
        context["form_data"] = {}
        context["form_action"] = "/people/attendance/shifts/new"
        context["is_edit"] = False
        return templates.TemplateResponse(request, "people/attendance/shift_form.html", context)

    @staticmethod
    async def create_shift_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Create a new shift type."""
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()
        shift_code = AttendanceWebService._get_form_str(form, "shift_code")
        shift_name = AttendanceWebService._get_form_str(form, "shift_name")
        start_time = AttendanceWebService._get_form_str(form, "start_time")
        end_time = AttendanceWebService._get_form_str(form, "end_time")
        working_hours = AttendanceWebService._get_form_str(form, "working_hours")
        description = AttendanceWebService._get_form_str(form, "description")
        late_entry_grace_period = AttendanceWebService._get_form_str(form, "late_entry_grace_period")
        early_exit_grace_period = AttendanceWebService._get_form_str(form, "early_exit_grace_period")
        enable_half_day = AttendanceWebService._get_form_str(form, "enable_half_day")
        half_day_threshold_hours = AttendanceWebService._get_form_str(form, "half_day_threshold_hours")
        enable_overtime = AttendanceWebService._get_form_str(form, "enable_overtime")
        overtime_threshold_hours = AttendanceWebService._get_form_str(form, "overtime_threshold_hours")
        break_duration_minutes = AttendanceWebService._get_form_str(form, "break_duration_minutes")
        is_active = AttendanceWebService._get_form_str(form, "is_active")

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
                start_time=AttendanceWebService._parse_time(start_time),
                end_time=AttendanceWebService._parse_time(end_time),
                working_hours=AttendanceWebService._parse_decimal(working_hours),
                description=description or None,
                late_entry_grace_period=AttendanceWebService._parse_int(late_entry_grace_period, 0),
                early_exit_grace_period=AttendanceWebService._parse_int(early_exit_grace_period, 0),
                enable_half_day=AttendanceWebService._parse_bool(enable_half_day, False),
                half_day_threshold_hours=AttendanceWebService._parse_decimal(half_day_threshold_hours),
                enable_overtime=AttendanceWebService._parse_bool(enable_overtime, False),
                overtime_threshold_hours=AttendanceWebService._parse_decimal(overtime_threshold_hours),
                break_duration_minutes=AttendanceWebService._parse_int(break_duration_minutes, 60),
                is_active=AttendanceWebService._parse_bool(is_active, False),
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

    @staticmethod
    def edit_shift_form_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        shift_type_id: str,
    ) -> HTMLResponse:
        """Edit shift type form."""
        svc = AttendanceService(db)
        org_id = coerce_uuid(auth.organization_id)
        shift = svc.get_shift_type(org_id, coerce_uuid(shift_type_id))
        context = base_context(request, auth, "Edit Shift Type", "attendance", db=db)
        context["request"] = request
        context["form_data"] = AttendanceWebService._shift_form_context(
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

    @staticmethod
    async def update_shift_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        shift_type_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Update a shift type."""
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()
        shift_code = AttendanceWebService._get_form_str(form, "shift_code")
        shift_name = AttendanceWebService._get_form_str(form, "shift_name")
        start_time = AttendanceWebService._get_form_str(form, "start_time")
        end_time = AttendanceWebService._get_form_str(form, "end_time")
        working_hours = AttendanceWebService._get_form_str(form, "working_hours")
        description = AttendanceWebService._get_form_str(form, "description")
        late_entry_grace_period = AttendanceWebService._get_form_str(form, "late_entry_grace_period")
        early_exit_grace_period = AttendanceWebService._get_form_str(form, "early_exit_grace_period")
        enable_half_day = AttendanceWebService._get_form_str(form, "enable_half_day")
        half_day_threshold_hours = AttendanceWebService._get_form_str(form, "half_day_threshold_hours")
        enable_overtime = AttendanceWebService._get_form_str(form, "enable_overtime")
        overtime_threshold_hours = AttendanceWebService._get_form_str(form, "overtime_threshold_hours")
        break_duration_minutes = AttendanceWebService._get_form_str(form, "break_duration_minutes")
        is_active = AttendanceWebService._get_form_str(form, "is_active")

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
                start_time=AttendanceWebService._parse_time(start_time),
                end_time=AttendanceWebService._parse_time(end_time),
                working_hours=AttendanceWebService._parse_decimal(working_hours),
                description=description or None,
                late_entry_grace_period=AttendanceWebService._parse_int(late_entry_grace_period, 0),
                early_exit_grace_period=AttendanceWebService._parse_int(early_exit_grace_period, 0),
                enable_half_day=AttendanceWebService._parse_bool(enable_half_day, False),
                half_day_threshold_hours=AttendanceWebService._parse_decimal(half_day_threshold_hours),
                enable_overtime=AttendanceWebService._parse_bool(enable_overtime, False),
                overtime_threshold_hours=AttendanceWebService._parse_decimal(overtime_threshold_hours),
                break_duration_minutes=AttendanceWebService._parse_int(break_duration_minutes, 60),
                is_active=AttendanceWebService._parse_bool(is_active, False),
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

    @staticmethod
    def attendance_summary_report_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        start_date: Optional[str],
        end_date: Optional[str],
        department_id: Optional[str],
    ) -> HTMLResponse:
        """Attendance summary report page."""
        from app.services.people.hr import OrganizationService, DepartmentFilters

        org_id = coerce_uuid(auth.organization_id)
        svc = AttendanceService(db)
        org_svc = OrganizationService(db, org_id)

        report = svc.get_attendance_summary_report(
            org_id,
            start_date=AttendanceWebService._parse_date(start_date),
            end_date=AttendanceWebService._parse_date(end_date),
            department_id=AttendanceWebService._parse_uuid(department_id),
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

    @staticmethod
    def attendance_by_employee_report_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        start_date: Optional[str],
        end_date: Optional[str],
        department_id: Optional[str],
    ) -> HTMLResponse:
        """Attendance by employee report page."""
        from app.services.people.hr import OrganizationService, DepartmentFilters

        org_id = coerce_uuid(auth.organization_id)
        svc = AttendanceService(db)
        org_svc = OrganizationService(db, org_id)

        report = svc.get_attendance_by_employee_report(
            org_id,
            start_date=AttendanceWebService._parse_date(start_date),
            end_date=AttendanceWebService._parse_date(end_date),
            department_id=AttendanceWebService._parse_uuid(department_id),
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

    @staticmethod
    def attendance_late_early_report_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        start_date: Optional[str],
        end_date: Optional[str],
        department_id: Optional[str],
    ) -> HTMLResponse:
        """Late arrivals and early departures report page."""
        from app.services.people.hr import OrganizationService, DepartmentFilters

        org_id = coerce_uuid(auth.organization_id)
        svc = AttendanceService(db)
        org_svc = OrganizationService(db, org_id)

        report = svc.get_late_early_report(
            org_id,
            start_date=AttendanceWebService._parse_date(start_date),
            end_date=AttendanceWebService._parse_date(end_date),
            department_id=AttendanceWebService._parse_uuid(department_id),
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

    @staticmethod
    def attendance_trends_report_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        months: int,
    ) -> HTMLResponse:
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

    @staticmethod
    def attendance_requests_list_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
        page: int,
        success: Optional[str],
        error: Optional[str],
    ) -> HTMLResponse:
        """Attendance requests list page."""
        from app.models.people.attendance import AttendanceRequestStatus

        org_id = coerce_uuid(auth.organization_id)
        pagination = PaginationParams.from_page(page, per_page=20)
        svc = AttendanceService(db)

        status_enum = None
        if status:
            try:
                status_enum = AttendanceRequestStatus(status)
            except ValueError:
                status_enum = None

        result = svc.list_attendance_requests(
            org_id,
            from_date=AttendanceWebService._parse_date(start_date),
            to_date=AttendanceWebService._parse_date(end_date),
            status=status_enum,
            pagination=pagination,
        )

        pending_result = svc.list_attendance_requests(
            org_id,
            status=AttendanceRequestStatus.PENDING,
            pagination=PaginationParams(offset=0, limit=1),
        )

        context = base_context(request, auth, "Attendance Requests", "attendance", db=db)
        context.update({
            "requests": result.items,
            "pending_count": pending_result.total,
            "statuses": [s.value for s in AttendanceRequestStatus],
            "status": status,
            "start_date": start_date,
            "end_date": end_date,
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
            "success": success,
            "error": error,
        })
        return templates.TemplateResponse(request, "people/attendance/requests.html", context)

    @staticmethod
    def attendance_request_new_form_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        form_data: Optional[dict] = None,
        error: Optional[str] = None,
    ) -> HTMLResponse:
        """New attendance request form."""
        org_id = coerce_uuid(auth.organization_id)
        employees = AttendanceWebService._get_employees(db, org_id)

        context = base_context(request, auth, "New Attendance Request", "attendance", db=db)
        context.update({
            "employees": employees,
            "form_data": form_data or {},
            "error": error,
        })
        return templates.TemplateResponse(request, "people/attendance/request_form.html", context)

    async def create_attendance_request_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Create a new attendance request."""
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        org_id = coerce_uuid(auth.organization_id)
        svc = AttendanceService(db)

        employee_id = AttendanceWebService._get_form_str(form, "employee_id")
        from_date_str = AttendanceWebService._get_form_str(form, "from_date")
        to_date_str = AttendanceWebService._get_form_str(form, "to_date")
        half_day = AttendanceWebService._get_form_str(form, "half_day") == "true"
        half_day_date_str = AttendanceWebService._get_form_str(form, "half_day_date")
        reason = AttendanceWebService._get_form_str(form, "reason") or None
        explanation = AttendanceWebService._get_form_str(form, "explanation") or None

        if not employee_id or not from_date_str or not to_date_str:
            return self.attendance_request_new_form_response(
                request=request,
                auth=auth,
                db=db,
                form_data=dict(form),
                error="Employee, from date, and to date are required.",
            )

        from_date = AttendanceWebService._parse_date(from_date_str)
        to_date = AttendanceWebService._parse_date(to_date_str)
        half_day_date = AttendanceWebService._parse_date(half_day_date_str) if half_day else None

        if not from_date or not to_date:
            return self.attendance_request_new_form_response(
                request=request,
                auth=auth,
                db=db,
                form_data=dict(form),
                error="From date and to date must be valid dates.",
            )

        try:
            svc.create_attendance_request(
                org_id,
                employee_id=coerce_uuid(employee_id),
                from_date=from_date,
                to_date=to_date,
                half_day=half_day,
                half_day_date=half_day_date,
                reason=reason,
                explanation=explanation,
            )
            db.commit()
            success_msg = quote("Attendance request submitted")
            return RedirectResponse(url=f"/people/attendance/requests?success={success_msg}", status_code=303)
        except Exception as e:
            db.rollback()
            return self.attendance_request_new_form_response(
                request=request,
                auth=auth,
                db=db,
                form_data=dict(form),
                error=str(e),
            )

    @staticmethod
    def approve_attendance_request_response(
        auth: WebAuthContext,
        db: Session,
        request_id: str,
    ) -> RedirectResponse:
        """Approve an attendance request."""
        org_id = coerce_uuid(auth.organization_id)
        svc = AttendanceService(db)
        svc.approve_attendance_request(org_id, coerce_uuid(request_id))
        db.commit()
        success_msg = quote("Attendance request approved successfully")
        return RedirectResponse(url=f"/people/attendance/requests?success={success_msg}", status_code=303)

    @staticmethod
    def reject_attendance_request_response(
        auth: WebAuthContext,
        db: Session,
        request_id: str,
    ) -> RedirectResponse:
        """Reject an attendance request."""
        org_id = coerce_uuid(auth.organization_id)
        svc = AttendanceService(db)
        svc.reject_attendance_request(org_id, coerce_uuid(request_id))
        db.commit()
        success_msg = quote("Attendance request rejected")
        return RedirectResponse(url=f"/people/attendance/requests?success={success_msg}", status_code=303)

    @staticmethod
    async def bulk_approve_attendance_requests_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Bulk approve attendance requests."""
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        request_ids = form.getlist("request_ids")
        if not request_ids:
            return RedirectResponse(
                url="/people/attendance/requests?error=No+requests+selected",
                status_code=303
            )

        valid_ids = []
        for req_id in request_ids:
            try:
                valid_ids.append(coerce_uuid(req_id))
            except Exception:
                pass

        if not valid_ids:
            return RedirectResponse(
                url="/people/attendance/requests?error=No+valid+requests+selected",
                status_code=303
            )

        org_id = coerce_uuid(auth.organization_id)
        svc = AttendanceService(db)
        result = svc.bulk_approve_attendance_requests(org_id, valid_ids)
        db.commit()

        success_msg = quote(f"Approved {result['approved']} request(s)")
        return RedirectResponse(url=f"/people/attendance/requests?success={success_msg}", status_code=303)

    @staticmethod
    async def bulk_reject_attendance_requests_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Bulk reject attendance requests."""
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        request_ids = form.getlist("request_ids")
        if not request_ids:
            return RedirectResponse(
                url="/people/attendance/requests?error=No+requests+selected",
                status_code=303
            )

        valid_ids = []
        for req_id in request_ids:
            try:
                valid_ids.append(coerce_uuid(req_id))
            except Exception:
                pass

        if not valid_ids:
            return RedirectResponse(
                url="/people/attendance/requests?error=No+valid+requests+selected",
                status_code=303
            )

        org_id = coerce_uuid(auth.organization_id)
        svc = AttendanceService(db)
        result = svc.bulk_reject_attendance_requests(org_id, valid_ids)
        db.commit()

        success_msg = quote(f"Rejected {result['rejected']} request(s)")
        return RedirectResponse(url=f"/people/attendance/requests?success={success_msg}", status_code=303)


attendance_web_service = AttendanceWebService()
