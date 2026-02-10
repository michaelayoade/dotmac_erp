"""
Scheduling web view service.

Provides view-focused data and operations for scheduling web routes.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, cast
from urllib.parse import quote
from uuid import UUID

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.models.people.attendance.shift_type import ShiftType
from app.models.people.hr.department import Department
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.scheduling import (
    RotationType,
    SwapRequestStatus,
)
from app.models.person import Person
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.scheduling import (
    ScheduleGenerator,
    SchedulingService,
    SwapService,
)
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


class SchedulingWebService:
    """Web service methods for scheduling pages."""

    @staticmethod
    def _parse_date(value: str | None) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _parse_uuid(value: str | None) -> UUID | None:
        if not value:
            return None
        try:
            return cast(UUID, coerce_uuid(value))
        except Exception:
            return None

    @staticmethod
    def _get_form_str(form: Any, key: str, default: str = "") -> str:
        value = form.get(key, default) if form is not None else default
        if isinstance(value, UploadFile) or value is None:
            return default
        return str(value).strip()

    @staticmethod
    def _get_employees(db: Session, org_id: UUID) -> list[Employee]:
        """Get active employees for dropdowns."""
        stmt = (
            select(Employee)
            .join(Person, Employee.person_id == Person.id)
            .where(Employee.organization_id == org_id)
            .where(Employee.status == EmployeeStatus.ACTIVE)
            .order_by(Person.first_name.asc(), Person.last_name.asc())
        )
        return list(db.scalars(stmt).all())

    @staticmethod
    def _get_departments(db: Session, org_id: UUID) -> list[Department]:
        """Get active departments for dropdowns."""
        stmt = (
            select(Department)
            .where(Department.organization_id == org_id)
            .where(Department.is_active == True)  # noqa: E712
            .order_by(Department.department_name)
        )
        return list(db.scalars(stmt).all())

    @staticmethod
    def _get_shift_types(db: Session, org_id: UUID) -> list[ShiftType]:
        """Get active shift types for dropdowns."""
        stmt = (
            select(ShiftType)
            .where(ShiftType.organization_id == org_id)
            .where(ShiftType.is_active == True)  # noqa: E712
            .order_by(ShiftType.shift_name)
        )
        return list(db.scalars(stmt).all())

    # =========================================================================
    # Shift Patterns
    # =========================================================================

    @staticmethod
    def patterns_list_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None,
        is_active: str | None,
        page: int,
    ) -> HTMLResponse:
        """Shift patterns list page."""
        org_id = coerce_uuid(auth.organization_id)
        pagination = PaginationParams.from_page(page, per_page=20)
        svc = SchedulingService(db)

        is_active_bool = None
        if is_active == "true":
            is_active_bool = True
        elif is_active == "false":
            is_active_bool = False

        result = svc.list_patterns(
            org_id,
            search=search,
            is_active=is_active_bool,
            pagination=pagination,
        )

        patterns = []
        for p in result.items:
            patterns.append(
                {
                    "shift_pattern_id": str(p.shift_pattern_id),
                    "pattern_code": p.pattern_code,
                    "pattern_name": p.pattern_name,
                    "rotation_type": p.rotation_type.value,
                    "cycle_weeks": p.cycle_weeks,
                    "work_days": p.work_days,
                    "day_shift_name": p.day_shift_type.shift_name
                    if p.day_shift_type
                    else "",
                    "night_shift_name": p.night_shift_type.shift_name
                    if p.night_shift_type
                    else "-",
                    "is_active": p.is_active,
                }
            )

        total_pages = (
            (result.total + pagination.limit - 1) // pagination.limit
            if pagination.limit > 0
            else 1
        )

        ctx = base_context(request, auth, "Shift Patterns", "people", db=db)
        ctx.update(
            {
                "patterns": patterns,
                "search": search or "",
                "is_active": is_active or "",
                "page": page,
                "total_pages": total_pages,
                "has_prev": page > 1,
                "has_next": page < total_pages,
                "rotation_types": [r.value for r in RotationType],
            }
        )

        return templates.TemplateResponse("people/scheduling/patterns.html", ctx)

    @staticmethod
    def new_pattern_form_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """New pattern form."""
        org_id = coerce_uuid(auth.organization_id)
        shift_types = SchedulingWebService._get_shift_types(db, org_id)

        ctx = base_context(request, auth, "New Shift Pattern", "people", db=db)
        ctx.update(
            {
                "shift_types": shift_types,
                "rotation_types": [r.value for r in RotationType],
                "work_days_options": ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"],
                "pattern": {},
                "error": None,
            }
        )

        return templates.TemplateResponse("people/scheduling/pattern_form.html", ctx)

    @staticmethod
    async def create_pattern_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Create a new pattern."""
        org_id = coerce_uuid(auth.organization_id)
        form_data = getattr(request.state, "csrf_form", None)
        if form_data is None:
            form_data = await request.form()

        pattern_code = SchedulingWebService._get_form_str(form_data, "pattern_code")
        pattern_name = SchedulingWebService._get_form_str(form_data, "pattern_name")
        description = (
            SchedulingWebService._get_form_str(form_data, "description") or None
        )
        rotation_type = (
            SchedulingWebService._get_form_str(form_data, "rotation_type", "DAY_ONLY")
            or "DAY_ONLY"
        )
        cycle_weeks = int(
            SchedulingWebService._get_form_str(form_data, "cycle_weeks", "1") or "1"
        )
        work_days = [
            day for day in form_data.getlist("work_days") if isinstance(day, str)
        ]
        day_shift_type_id = SchedulingWebService._parse_uuid(
            SchedulingWebService._get_form_str(form_data, "day_shift_type_id")
        )
        night_shift_type_id = SchedulingWebService._parse_uuid(
            SchedulingWebService._get_form_str(form_data, "night_shift_type_id")
        )

        if not pattern_code or not pattern_name or day_shift_type_id is None:
            shift_types = SchedulingWebService._get_shift_types(db, org_id)
            if not shift_types:
                error_message = "No shift types available. Create a shift type first."
            else:
                error_message = "Pattern code, name, and day shift type are required."
            ctx = base_context(request, auth, "New Shift Pattern", "people", db=db)
            ctx.update(
                {
                    "shift_types": shift_types,
                    "rotation_types": [r.value for r in RotationType],
                    "work_days_options": [
                        "MON",
                        "TUE",
                        "WED",
                        "THU",
                        "FRI",
                        "SAT",
                        "SUN",
                    ],
                    "pattern": {
                        "pattern_code": pattern_code,
                        "pattern_name": pattern_name,
                        "description": description,
                        "rotation_type": rotation_type,
                        "cycle_weeks": cycle_weeks,
                        "work_days": work_days,
                    },
                    "error": error_message,
                }
            )
            return templates.TemplateResponse(
                "people/scheduling/pattern_form.html", ctx
            )

        try:
            if day_shift_type_id is None:
                raise ValueError("Day shift type is required")
            svc = SchedulingService(db)
            svc.create_pattern(
                org_id=org_id,
                pattern_code=pattern_code,
                pattern_name=pattern_name,
                description=description,
                rotation_type=RotationType(rotation_type),
                cycle_weeks=cycle_weeks,
                work_days=work_days
                if work_days
                else ["MON", "TUE", "WED", "THU", "FRI"],
                day_shift_type_id=day_shift_type_id,
                night_shift_type_id=night_shift_type_id,
            )
            db.commit()
            return RedirectResponse(
                "/people/scheduling/patterns?success=created", status_code=303
            )
        except Exception as e:
            db.rollback()
            shift_types = SchedulingWebService._get_shift_types(db, org_id)
            ctx = base_context(request, auth, "New Shift Pattern", "people", db=db)
            ctx.update(
                {
                    "shift_types": shift_types,
                    "rotation_types": [r.value for r in RotationType],
                    "work_days_options": [
                        "MON",
                        "TUE",
                        "WED",
                        "THU",
                        "FRI",
                        "SAT",
                        "SUN",
                    ],
                    "pattern": {
                        "pattern_code": pattern_code,
                        "pattern_name": pattern_name,
                        "description": description,
                        "rotation_type": rotation_type,
                        "cycle_weeks": cycle_weeks,
                        "work_days": work_days,
                    },
                    "error": str(e),
                }
            )
            return templates.TemplateResponse(
                "people/scheduling/pattern_form.html", ctx
            )

    @staticmethod
    def edit_pattern_form_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        pattern_id: str,
    ) -> HTMLResponse:
        """Edit pattern form."""
        org_id = coerce_uuid(auth.organization_id)
        pattern_uuid = coerce_uuid(pattern_id)
        svc = SchedulingService(db)
        pattern = svc.get_pattern(org_id, pattern_uuid)
        shift_types = SchedulingWebService._get_shift_types(db, org_id)

        ctx = base_context(request, auth, "Edit Shift Pattern", "people", db=db)
        ctx.update(
            {
                "shift_types": shift_types,
                "rotation_types": [r.value for r in RotationType],
                "work_days_options": ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"],
                "pattern": {
                    "shift_pattern_id": str(pattern.shift_pattern_id),
                    "pattern_code": pattern.pattern_code,
                    "pattern_name": pattern.pattern_name,
                    "description": pattern.description or "",
                    "rotation_type": pattern.rotation_type.value,
                    "cycle_weeks": pattern.cycle_weeks,
                    "work_days": pattern.work_days,
                    "day_shift_type_id": str(pattern.day_shift_type_id),
                    "night_shift_type_id": str(pattern.night_shift_type_id)
                    if pattern.night_shift_type_id
                    else "",
                    "is_active": pattern.is_active,
                },
                "error": None,
                "is_edit": True,
            }
        )

        return templates.TemplateResponse("people/scheduling/pattern_form.html", ctx)

    @staticmethod
    async def update_pattern_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        pattern_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Update a pattern."""
        org_id = coerce_uuid(auth.organization_id)
        pattern_uuid = coerce_uuid(pattern_id)
        form_data = await request.form()

        try:
            svc = SchedulingService(db)
            work_days = [
                day for day in form_data.getlist("work_days") if isinstance(day, str)
            ]
            night_shift_type_id = SchedulingWebService._parse_uuid(
                SchedulingWebService._get_form_str(form_data, "night_shift_type_id")
            )

            svc.update_pattern(
                org_id,
                pattern_uuid,
                pattern_code=SchedulingWebService._get_form_str(
                    form_data, "pattern_code"
                ),
                pattern_name=SchedulingWebService._get_form_str(
                    form_data, "pattern_name"
                ),
                description=SchedulingWebService._get_form_str(form_data, "description")
                or None,
                rotation_type=RotationType(
                    SchedulingWebService._get_form_str(
                        form_data, "rotation_type", "DAY_ONLY"
                    )
                    or "DAY_ONLY"
                ),
                cycle_weeks=int(
                    SchedulingWebService._get_form_str(form_data, "cycle_weeks", "1")
                    or "1"
                ),
                work_days=work_days if work_days else None,
                day_shift_type_id=SchedulingWebService._parse_uuid(
                    SchedulingWebService._get_form_str(form_data, "day_shift_type_id")
                ),
                night_shift_type_id=night_shift_type_id,
                is_active=SchedulingWebService._get_form_str(form_data, "is_active")
                == "on",
            )
            db.commit()
            return RedirectResponse(
                "/people/scheduling/patterns?success=updated", status_code=303
            )
        except Exception:
            db.rollback()
            return SchedulingWebService.edit_pattern_form_response(
                request, auth, db, pattern_id
            )

    # =========================================================================
    # Pattern Assignments
    # =========================================================================

    @staticmethod
    def assignments_list_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        department_id: str | None,
        page: int,
    ) -> HTMLResponse:
        """Pattern assignments list page."""
        org_id = coerce_uuid(auth.organization_id)
        pagination = PaginationParams.from_page(page, per_page=20)
        svc = SchedulingService(db)

        dept_uuid = SchedulingWebService._parse_uuid(department_id)

        result = svc.list_assignments(
            org_id,
            department_id=dept_uuid,
            is_active=True,
            pagination=pagination,
        )

        assignments = []
        for a in result.items:
            assignments.append(
                {
                    "pattern_assignment_id": str(a.pattern_assignment_id),
                    "employee_name": a.employee.full_name if a.employee else "",
                    "employee_code": a.employee.employee_code if a.employee else "",
                    "department_name": a.department.department_name
                    if a.department
                    else "",
                    "pattern_name": a.shift_pattern.pattern_name
                    if a.shift_pattern
                    else "",
                    "pattern_code": a.shift_pattern.pattern_code
                    if a.shift_pattern
                    else "",
                    "rotation_week_offset": a.rotation_week_offset,
                    "effective_from": a.effective_from.isoformat()
                    if a.effective_from
                    else "",
                    "effective_to": a.effective_to.isoformat()
                    if a.effective_to
                    else "-",
                    "is_active": a.is_active,
                }
            )

        departments = SchedulingWebService._get_departments(db, org_id)
        total_pages = (
            (result.total + pagination.limit - 1) // pagination.limit
            if pagination.limit > 0
            else 1
        )

        ctx = base_context(request, auth, "Shift Assignments", "people", db=db)
        ctx.update(
            {
                "assignments": assignments,
                "departments": departments,
                "department_id": department_id or "",
                "page": page,
                "total_pages": total_pages,
                "has_prev": page > 1,
                "has_next": page < total_pages,
            }
        )

        return templates.TemplateResponse("people/scheduling/assignments.html", ctx)

    @staticmethod
    def new_assignment_form_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """New assignment form."""
        org_id = coerce_uuid(auth.organization_id)
        employees = SchedulingWebService._get_employees(db, org_id)
        departments = SchedulingWebService._get_departments(db, org_id)
        svc = SchedulingService(db)
        patterns_result = svc.list_patterns(org_id, is_active=True)

        ctx = base_context(request, auth, "New Shift Assignment", "people", db=db)
        ctx.update(
            {
                "employees": employees,
                "departments": departments,
                "patterns": patterns_result.items,
                "assignment": {},
                "error": None,
            }
        )

        return templates.TemplateResponse("people/scheduling/assignment_form.html", ctx)

    @staticmethod
    async def create_assignment_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Create a new assignment."""
        org_id = coerce_uuid(auth.organization_id)
        form_data = await request.form()

        employee_id = SchedulingWebService._parse_uuid(
            SchedulingWebService._get_form_str(form_data, "employee_id")
        )
        department_id = SchedulingWebService._parse_uuid(
            SchedulingWebService._get_form_str(form_data, "department_id")
        )
        shift_pattern_id = SchedulingWebService._parse_uuid(
            SchedulingWebService._get_form_str(form_data, "shift_pattern_id")
        )
        effective_from = SchedulingWebService._parse_date(
            SchedulingWebService._get_form_str(form_data, "effective_from")
        )
        effective_to = SchedulingWebService._parse_date(
            SchedulingWebService._get_form_str(form_data, "effective_to")
        )
        rotation_week_offset = int(
            SchedulingWebService._get_form_str(form_data, "rotation_week_offset", "0")
            or "0"
        )

        if (
            employee_id is None
            or department_id is None
            or shift_pattern_id is None
            or effective_from is None
        ):
            employees = SchedulingWebService._get_employees(db, org_id)
            departments = SchedulingWebService._get_departments(db, org_id)
            svc = SchedulingService(db)
            patterns_result = svc.list_patterns(org_id, is_active=True)

            ctx = base_context(request, auth, "New Shift Assignment", "people", db=db)
            ctx.update(
                {
                    "employees": employees,
                    "departments": departments,
                    "patterns": patterns_result.items,
                    "assignment": {},
                    "error": "Employee, department, pattern, and effective from date are required.",
                }
            )
            return templates.TemplateResponse(
                "people/scheduling/assignment_form.html", ctx
            )

        try:
            svc = SchedulingService(db)
            svc.create_assignment(
                org_id=org_id,
                employee_id=employee_id,
                department_id=department_id,
                shift_pattern_id=shift_pattern_id,
                effective_from=effective_from,
                effective_to=effective_to,
                rotation_week_offset=rotation_week_offset,
            )
            db.commit()
            return RedirectResponse(
                "/people/scheduling/assignments?success=created", status_code=303
            )
        except Exception as e:
            db.rollback()
            employees = SchedulingWebService._get_employees(db, org_id)
            departments = SchedulingWebService._get_departments(db, org_id)
            svc = SchedulingService(db)
            patterns_result = svc.list_patterns(org_id, is_active=True)

            ctx = base_context(request, auth, "New Shift Assignment", "people", db=db)
            ctx.update(
                {
                    "employees": employees,
                    "departments": departments,
                    "patterns": patterns_result.items,
                    "assignment": {},
                    "error": str(e),
                }
            )
            return templates.TemplateResponse(
                "people/scheduling/assignment_form.html", ctx
            )

    # =========================================================================
    # Schedules
    # =========================================================================

    @staticmethod
    def schedules_list_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        department_id: str | None,
        year_month: str | None,
        page: int,
    ) -> HTMLResponse:
        """Schedules list/calendar page."""
        org_id = coerce_uuid(auth.organization_id)
        pagination = PaginationParams.from_page(page, per_page=100)
        svc = SchedulingService(db)

        dept_uuid = SchedulingWebService._parse_uuid(department_id)

        # Default to current month
        if not year_month:
            year_month = date.today().strftime("%Y-%m")

        result = svc.list_schedules(
            org_id,
            department_id=dept_uuid,
            schedule_month=year_month,
            pagination=pagination,
        )

        schedules = []
        for s in result.items:
            schedules.append(
                {
                    "shift_schedule_id": str(s.shift_schedule_id),
                    "employee_name": s.employee.full_name if s.employee else "",
                    "employee_code": s.employee.employee_code if s.employee else "",
                    "shift_date": s.shift_date.isoformat(),
                    "shift_date_display": s.shift_date.strftime("%a %d"),
                    "shift_name": s.shift_type.shift_name if s.shift_type else "",
                    "shift_code": s.shift_type.shift_code if s.shift_type else "",
                    "status": s.status.value,
                }
            )

        departments = SchedulingWebService._get_departments(db, org_id)
        schedule_status = (
            svc.get_schedule_status_for_month(org_id, dept_uuid, year_month)
            if dept_uuid
            else None
        )

        ctx = base_context(request, auth, "Schedules", "people", db=db)
        ctx.update(
            {
                "schedules": schedules,
                "departments": departments,
                "department_id": department_id or "",
                "year_month": year_month,
                "schedule_status": schedule_status.value if schedule_status else None,
                "page": page,
            }
        )

        return templates.TemplateResponse("people/scheduling/schedules.html", ctx)

    @staticmethod
    def generate_schedule_form_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Generate schedule form."""
        org_id = coerce_uuid(auth.organization_id)
        departments = SchedulingWebService._get_departments(db, org_id)

        ctx = base_context(request, auth, "Generate Schedule", "people", db=db)
        ctx.update(
            {
                "departments": departments,
                "department_id": "",
                "year_month": date.today().strftime("%Y-%m"),
                "error": None,
                "result": None,
            }
        )

        return templates.TemplateResponse(
            "people/scheduling/schedule_generate.html", ctx
        )

    @staticmethod
    async def generate_schedule_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Generate schedules for a month."""
        org_id = coerce_uuid(auth.organization_id)
        form_data = getattr(request.state, "csrf_form", None)
        if form_data is None:
            form_data = await request.form()

        department_id = SchedulingWebService._parse_uuid(
            SchedulingWebService._get_form_str(form_data, "department_id")
        )
        year_month = SchedulingWebService._get_form_str(form_data, "year_month")

        departments = SchedulingWebService._get_departments(db, org_id)

        if not department_id or not year_month:
            ctx = base_context(request, auth, "Generate Schedule", "people", db=db)
            ctx.update(
                {
                    "departments": departments,
                    "department_id": SchedulingWebService._get_form_str(
                        form_data, "department_id"
                    ),
                    "year_month": year_month or date.today().strftime("%Y-%m"),
                    "error": "Department and month are required.",
                    "result": None,
                }
            )
            return templates.TemplateResponse(
                "people/scheduling/schedule_generate.html", ctx
            )

        try:
            generator = ScheduleGenerator(db)
            result = generator.generate_monthly_schedule(
                org_id=org_id,
                department_id=department_id,
                year_month=year_month,
            )
            db.commit()

            ctx = base_context(request, auth, "Generate Schedule", "people", db=db)
            ctx.update(
                {
                    "departments": departments,
                    "department_id": SchedulingWebService._get_form_str(
                        form_data, "department_id"
                    ),
                    "year_month": year_month,
                    "error": None,
                    "result": result,
                }
            )
            return templates.TemplateResponse(
                "people/scheduling/schedule_generate.html", ctx
            )
        except Exception as e:
            db.rollback()
            ctx = base_context(request, auth, "Generate Schedule", "people", db=db)
            ctx.update(
                {
                    "departments": departments,
                    "department_id": SchedulingWebService._get_form_str(
                        form_data, "department_id"
                    ),
                    "year_month": year_month,
                    "error": str(e),
                    "result": None,
                }
            )
            return templates.TemplateResponse(
                "people/scheduling/schedule_generate.html", ctx
            )

    @staticmethod
    async def publish_schedule_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Publish schedules for a month."""
        org_id = coerce_uuid(auth.organization_id)
        form_data = getattr(request.state, "csrf_form", None)
        if form_data is None:
            form_data = await request.form()

        department_id = SchedulingWebService._parse_uuid(
            SchedulingWebService._get_form_str(form_data, "department_id")
        )
        year_month = SchedulingWebService._get_form_str(form_data, "year_month")

        if not department_id or not year_month:
            return RedirectResponse(
                "/people/scheduling/schedules?error=Department+and+month+are+required",
                status_code=303,
            )

        try:
            generator = ScheduleGenerator(db)
            generator.publish_schedule(
                org_id=org_id,
                department_id=department_id,
                year_month=year_month,
            )
            db.commit()
            return RedirectResponse(
                f"/people/scheduling/schedules?department_id={department_id}&year_month={year_month}&success=published",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            return RedirectResponse(
                f"/people/scheduling/schedules?department_id={department_id}&year_month={year_month}&error={quote(str(e))}",
                status_code=303,
            )

    # =========================================================================
    # Swap Requests
    # =========================================================================

    @staticmethod
    def swap_requests_list_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: str | None,
        page: int,
    ) -> HTMLResponse:
        """Swap requests list page."""
        org_id = coerce_uuid(auth.organization_id)
        pagination = PaginationParams.from_page(page, per_page=20)
        svc = SwapService(db)

        status_enum = None
        if status:
            try:
                status_enum = SwapRequestStatus(status)
            except ValueError:
                status_enum = None

        result = svc.list_swap_requests(
            org_id,
            status=status_enum,
            pagination=pagination,
        )

        swap_requests = []
        for sr in result.items:
            swap_requests.append(
                {
                    "swap_request_id": str(sr.swap_request_id),
                    "requester_name": sr.requester.full_name if sr.requester else "",
                    "target_name": sr.target_employee.full_name
                    if sr.target_employee
                    else "",
                    "requester_date": sr.requester_schedule.shift_date.isoformat()
                    if sr.requester_schedule
                    else "",
                    "target_date": sr.target_schedule.shift_date.isoformat()
                    if sr.target_schedule
                    else "",
                    "requester_shift": sr.requester_schedule.shift_type.shift_name
                    if sr.requester_schedule and sr.requester_schedule.shift_type
                    else "",
                    "target_shift": sr.target_schedule.shift_type.shift_name
                    if sr.target_schedule and sr.target_schedule.shift_type
                    else "",
                    "status": sr.status.value,
                    "reason": sr.reason or "",
                    "created_at": sr.created_at.strftime("%Y-%m-%d %H:%M")
                    if sr.created_at
                    else "",
                }
            )

        total_pages = (
            (result.total + pagination.limit - 1) // pagination.limit
            if pagination.limit > 0
            else 1
        )

        ctx = base_context(request, auth, "Swap Requests", "people", db=db)
        ctx.update(
            {
                "swap_requests": swap_requests,
                "status": status or "",
                "status_options": [s.value for s in SwapRequestStatus],
                "page": page,
                "total_pages": total_pages,
                "has_prev": page > 1,
                "has_next": page < total_pages,
            }
        )

        return templates.TemplateResponse("people/scheduling/swap_requests.html", ctx)

    @staticmethod
    async def approve_swap_request_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        request_id: str,
    ) -> RedirectResponse:
        """Approve a swap request."""
        org_id = coerce_uuid(auth.organization_id)
        request_uuid = coerce_uuid(request_id)

        # TODO: Get actual manager ID from auth context
        manager_id = coerce_uuid(auth.employee_id) if auth.employee_id else None
        if manager_id is None:
            return RedirectResponse(
                "/people/scheduling/swaps?error=Manager+not+found",
                status_code=303,
            )

        try:
            svc = SwapService(db)
            svc.approve_swap_request(
                org_id=org_id,
                request_id=request_uuid,
                manager_id=manager_id,
            )
            db.commit()
            return RedirectResponse(
                "/people/scheduling/swaps?success=approved",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            return RedirectResponse(
                f"/people/scheduling/swaps?error={quote(str(e))}",
                status_code=303,
            )

    @staticmethod
    async def reject_swap_request_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        request_id: str,
    ) -> RedirectResponse:
        """Reject a swap request."""
        org_id = coerce_uuid(auth.organization_id)
        request_uuid = coerce_uuid(request_id)
        form_data = await request.form()
        notes = SchedulingWebService._get_form_str(form_data, "notes") or None

        manager_id = coerce_uuid(auth.employee_id) if auth.employee_id else None
        if manager_id is None:
            return RedirectResponse(
                "/people/scheduling/swaps?error=Manager+not+found",
                status_code=303,
            )

        try:
            svc = SwapService(db)
            svc.reject_swap_request(
                org_id=org_id,
                request_id=request_uuid,
                manager_id=manager_id,
                notes=notes,
            )
            db.commit()
            return RedirectResponse(
                "/people/scheduling/swaps?success=rejected",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            return RedirectResponse(
                f"/people/scheduling/swaps?error={quote(str(e))}",
                status_code=303,
            )


# Singleton instance
scheduling_web_service = SchedulingWebService()
