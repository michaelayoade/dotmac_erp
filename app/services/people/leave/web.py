"""Leave web service helpers."""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload
from starlette.datastructures import UploadFile

from app.models.people.hr import Employee, EmployeeStatus
from app.models.people.leave import LeaveApplicationStatus
from app.models.person import Person
from app.services.common import PaginationParams, coerce_uuid
from app.services.common_filters import build_active_filters
from app.services.people.leave import LeaveAllocationExistsError, LeaveService
from app.services.people.leave.leave_service import (
    HolidayListNotFoundError,
    InsufficientLeaveBalanceError,
    LeaveAllocationNotFoundError,
    LeaveApplicationNotFoundError,
    LeaveServiceError,
    LeaveTypeNotFoundError,
)
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


class LeaveWebService:
    """Service layer for leave-related web routes."""

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
        except (ValueError, TypeError, AttributeError):
            return None

    @staticmethod
    def _parse_int(value: str | None) -> int | None:
        if value is None:
            return None
        value = str(value).strip()
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    @staticmethod
    def _parse_bool(value: str | None) -> bool | None:
        if value is None:
            return None
        value = str(value).strip().lower()
        if not value:
            return None
        if value in {"true", "1", "yes", "y"}:
            return True
        if value in {"false", "0", "no", "n"}:
            return False
        return None

    @staticmethod
    def _get_form_str(form: Any, key: str, default: str = "") -> str:
        value = form.get(key, default) if form is not None else default
        if isinstance(value, UploadFile) or value is None:
            return default
        return str(value).strip()

    @staticmethod
    def _get_employees(db: Session, org_id: UUID) -> list:
        """Get active employees for dropdowns."""
        return list(
            db.scalars(
                select(Employee)
                .options(
                    joinedload(Employee.person),
                    joinedload(Employee.manager).joinedload(Employee.person),
                )
                .where(
                    Employee.organization_id == org_id,
                    Employee.status == EmployeeStatus.ACTIVE,
                )
                .order_by(Employee.employee_code)
            ).all()
        )

    @staticmethod
    def _resolve_employee_filter(
        db: Session, org_id: UUID, value: str | None
    ) -> tuple[UUID | None, list[UUID] | None]:
        """Resolve employee filter from UUID, employee code, name, or email.

        Returns:
            - (employee_id, None) for exact UUID/code match
            - (None, [ids...]) for name/email/code partial matches
            - (None, [UUID(0)]) for non-empty unmatched terms (forces empty result)
            - (None, None) when no filter value is provided
        """
        raw = (value or "").strip()
        if not raw:
            return None, None

        parsed_uuid = LeaveWebService._parse_uuid(raw)
        if parsed_uuid:
            return parsed_uuid, None

        employee = db.scalar(
            select(Employee).where(
                Employee.organization_id == org_id,
                Employee.employee_code.ilike(raw),
            )
        )
        if employee:
            return employee.employee_id, None

        term = f"%{raw}%"
        matching_ids = list(
            db.scalars(
                select(Employee.employee_id)
                .join(Person, Person.id == Employee.person_id)
                .where(
                    Employee.organization_id == org_id,
                    or_(
                        Employee.employee_code.ilike(term),
                        Person.first_name.ilike(term),
                        Person.last_name.ilike(term),
                        Person.display_name.ilike(term),
                        Person.email.ilike(term),
                        func.concat(Person.first_name, " ", Person.last_name).ilike(
                            term
                        ),
                    ),
                )
                .limit(500)
            ).all()
        )
        if matching_ids:
            return None, matching_ids

        return None, [UUID(int=0)]

    def leave_overview_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Leave overview page."""
        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)
        stats = svc.get_leave_stats(org_id)
        context = base_context(request, auth, "Leave", "leave", db=db)
        context["stats"] = stats
        return templates.TemplateResponse(request, "people/leave/index.html", context)

    def leave_types_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None = None,
        is_active: bool | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Leave types list page."""
        org_id = coerce_uuid(auth.organization_id)
        pagination = PaginationParams.from_page(page, per_page=20)
        svc = LeaveService(db, auth)
        result = svc.list_leave_types(
            org_id,
            is_active=is_active,
            search=search,
            pagination=pagination,
        )
        context = base_context(request, auth, "Leave Types", "leave", db=db)
        context.update(
            {
                "types": result.items,
                "search": search,
                "is_active": is_active,
                "page": result.page,
                "total_pages": result.total_pages,
                "total": result.total,
                "has_prev": result.has_prev,
                "has_next": result.has_next,
            }
        )
        return templates.TemplateResponse(request, "people/leave/types.html", context)

    def leave_applications_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        employee_id: str | None = None,
        leave_type_id: str | None = None,
        status: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        page: int = 1,
        success: str | None = None,
        error: str | None = None,
    ) -> HTMLResponse:
        """Leave applications list page."""
        org_id = coerce_uuid(auth.organization_id)
        pagination = PaginationParams.from_page(page, per_page=20)
        status_enum = None
        if status:
            try:
                status_enum = LeaveApplicationStatus(status)
            except ValueError:
                status_enum = None
        svc = LeaveService(db, auth)
        result = svc.list_applications(
            org_id,
            employee_id=self._parse_uuid(employee_id),
            leave_type_id=self._parse_uuid(leave_type_id),
            status=status_enum,
            from_date=self._parse_date(start_date),
            to_date=self._parse_date(end_date),
            pagination=pagination,
        )
        context = base_context(request, auth, "Leave Applications", "leave", db=db)
        active_filters = build_active_filters(
            params={
                "status": status,
                "employee_id": employee_id,
                "leave_type_id": leave_type_id,
                "start_date": start_date,
                "end_date": end_date,
            },
            labels={"start_date": "From", "end_date": "To"},
        )
        context.update(
            {
                "applications": result.items,
                "employee_id": employee_id,
                "leave_type_id": leave_type_id,
                "status": status,
                "start_date": start_date,
                "end_date": end_date,
                "statuses": [s.value for s in LeaveApplicationStatus],
                "page": result.page,
                "total_pages": result.total_pages,
                "total": result.total,
                "has_prev": result.has_prev,
                "has_next": result.has_next,
                "success": success,
                "error": error,
                "active_filters": active_filters,
            }
        )
        return templates.TemplateResponse(
            request, "people/leave/applications.html", context
        )

    def leave_allocations_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        employee_id: str | None = None,
        leave_type_id: str | None = None,
        year: str | None = None,
        is_active: str | None = None,
        page: int = 1,
        per_page: int = 25,
        success: str | None = None,
        error: str | None = None,
    ) -> HTMLResponse:
        """Leave allocations list page."""
        org_id = coerce_uuid(auth.organization_id)
        parsed_year = self._parse_int(year)
        parsed_is_active = self._parse_bool(is_active)
        allowed_sizes = {25, 50, 75, 100}
        effective_per_page = per_page if per_page in allowed_sizes else 25
        pagination = PaginationParams.from_page(page, per_page=effective_per_page)
        svc = LeaveService(db, auth)
        resolved_employee_id, resolved_employee_ids = self._resolve_employee_filter(
            db, org_id, employee_id
        )
        result = svc.list_allocations(
            org_id,
            employee_id=resolved_employee_id,
            employee_ids=resolved_employee_ids,
            leave_type_id=self._parse_uuid(leave_type_id),
            year=parsed_year,
            is_active=parsed_is_active,
            pagination=pagination,
        )

        # Get data for bulk allocation dialog
        employees = self._get_employees(db, org_id)
        leave_types = svc.list_leave_types(org_id, is_active=True).items

        context = base_context(request, auth, "Leave Allocations", "leave", db=db)
        context.update(
            {
                "allocations": result.items,
                "employees": employees,
                "leave_types": leave_types,
                "today": date.today(),
                "employee_id": employee_id,
                "leave_type_id": leave_type_id,
                "year": parsed_year,
                "is_active": parsed_is_active,
                "page": result.page,
                "per_page": effective_per_page,
                "total_pages": result.total_pages,
                "total": result.total,
                "has_prev": result.has_prev,
                "has_next": result.has_next,
                "success": success,
                "error": error,
            }
        )
        return templates.TemplateResponse(
            request, "people/leave/allocations.html", context
        )

    def leave_holidays_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        year: int | None = None,
        is_active: bool | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Holiday lists page."""
        org_id = coerce_uuid(auth.organization_id)
        pagination = PaginationParams.from_page(page, per_page=20)
        svc = LeaveService(db, auth)
        result = svc.list_holiday_lists(
            org_id,
            year=year,
            is_active=is_active,
            pagination=pagination,
        )
        active_filters = build_active_filters(
            params={
                "is_active": str(is_active) if is_active is not None else None,
            },
            labels={"is_active": "Status"},
        )
        context = base_context(request, auth, "Holiday Lists", "leave", db=db)
        context.update(
            {
                "lists": result.items,
                "year": year,
                "is_active": is_active,
                "page": result.page,
                "total_pages": result.total_pages,
                "total": result.total,
                "total_count": result.total,
                "limit": result.limit,
                "has_prev": result.has_prev,
                "has_next": result.has_next,
                "active_filters": active_filters,
            }
        )
        return templates.TemplateResponse(
            request, "people/leave/holidays.html", context
        )

    def new_leave_type_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """New leave type form."""
        context = base_context(request, auth, "New Leave Type", "leave", db=db)
        return templates.TemplateResponse(
            request, "people/leave/leave_type_form.html", context
        )

    async def create_leave_type_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle leave type creation."""
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)

        leave_type_code = LeaveWebService._get_form_str(form, "leave_type_code")
        leave_type_name = LeaveWebService._get_form_str(form, "leave_type_name")

        if not leave_type_code or not leave_type_name:
            context = base_context(request, auth, "New Leave Type", "leave", db=db)
            context["error"] = "Leave type code and name are required."
            context["form_data"] = dict(form)
            return templates.TemplateResponse(
                request, "people/leave/leave_type_form.html", context
            )

        try:
            max_days = LeaveWebService._get_form_str(form, "max_days_per_year")
            max_continuous = LeaveWebService._get_form_str(form, "max_continuous_days")
            max_carry = LeaveWebService._get_form_str(form, "max_carry_forward_days")
            carry_forward_expiry = LeaveWebService._get_form_str(
                form, "carry_forward_expiry_months"
            )
            encash_threshold = LeaveWebService._get_form_str(
                form, "encashment_threshold_days"
            )
            applicable_after_days = LeaveWebService._get_form_str(
                form, "applicable_after_days"
            )
            max_optional_leaves = LeaveWebService._get_form_str(
                form, "max_optional_leaves"
            )

            svc.create_leave_type(
                org_id,
                leave_type_code=leave_type_code,
                leave_type_name=leave_type_name,
                max_days_per_year=Decimal(max_days) if max_days else None,
                max_continuous_days=int(max_continuous) if max_continuous else None,
                allow_carry_forward=LeaveWebService._get_form_str(
                    form, "allow_carry_forward"
                )
                == "true",
                max_carry_forward_days=Decimal(max_carry) if max_carry else None,
                carry_forward_expiry_months=int(carry_forward_expiry)
                if carry_forward_expiry
                else None,
                allow_encashment=LeaveWebService._get_form_str(form, "allow_encashment")
                == "true",
                encashment_threshold_days=Decimal(encash_threshold)
                if encash_threshold
                else None,
                is_lwp=LeaveWebService._get_form_str(form, "is_lwp") == "true",
                is_optional=LeaveWebService._get_form_str(form, "is_optional")
                == "true",
                is_compensatory=LeaveWebService._get_form_str(form, "is_compensatory")
                == "true",
                include_holidays=LeaveWebService._get_form_str(form, "include_holidays")
                == "true",
                applicable_after_days=int(applicable_after_days)
                if applicable_after_days
                else 0,
                max_optional_leaves=int(max_optional_leaves)
                if max_optional_leaves
                else None,
                is_active=LeaveWebService._get_form_str(form, "is_active") == "true",
                description=LeaveWebService._get_form_str(form, "description") or None,
            )
            db.commit()
            return RedirectResponse("/people/leave/types", status_code=303)
        except Exception as e:
            logger.exception("Failed to create leave type: %s", e)
            db.rollback()
            context = base_context(request, auth, "New Leave Type", "leave", db=db)
            context["error"] = str(e)
            context["form_data"] = dict(form)
            return templates.TemplateResponse(
                request, "people/leave/leave_type_form.html", context
            )

    def edit_leave_type_form_response(
        self,
        request: Request,
        leave_type_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Edit leave type form."""
        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)
        try:
            leave_type = svc.get_leave_type(org_id, coerce_uuid(leave_type_id))
        except LeaveTypeNotFoundError:
            return RedirectResponse("/people/leave/types", status_code=303)

        context = base_context(request, auth, "Edit Leave Type", "leave", db=db)
        context["leave_type"] = leave_type
        return templates.TemplateResponse(
            request, "people/leave/leave_type_form.html", context
        )

    async def update_leave_type_response(
        self,
        request: Request,
        leave_type_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle leave type update."""
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)

        try:
            max_days = LeaveWebService._get_form_str(form, "max_days_per_year")
            max_continuous = LeaveWebService._get_form_str(form, "max_continuous_days")
            max_carry = LeaveWebService._get_form_str(form, "max_carry_forward_days")
            carry_forward_expiry = LeaveWebService._get_form_str(
                form, "carry_forward_expiry_months"
            )
            encash_threshold = LeaveWebService._get_form_str(
                form, "encashment_threshold_days"
            )
            applicable_after_days = LeaveWebService._get_form_str(
                form, "applicable_after_days"
            )
            max_optional_leaves = LeaveWebService._get_form_str(
                form, "max_optional_leaves"
            )

            svc.update_leave_type(
                org_id,
                coerce_uuid(leave_type_id),
                leave_type_name=LeaveWebService._get_form_str(form, "leave_type_name"),
                max_days_per_year=Decimal(max_days) if max_days else None,
                max_continuous_days=int(max_continuous) if max_continuous else None,
                allow_carry_forward=LeaveWebService._get_form_str(
                    form, "allow_carry_forward"
                )
                == "true",
                max_carry_forward_days=Decimal(max_carry) if max_carry else None,
                carry_forward_expiry_months=int(carry_forward_expiry)
                if carry_forward_expiry
                else None,
                allow_encashment=LeaveWebService._get_form_str(form, "allow_encashment")
                == "true",
                encashment_threshold_days=Decimal(encash_threshold)
                if encash_threshold
                else None,
                is_lwp=LeaveWebService._get_form_str(form, "is_lwp") == "true",
                is_optional=LeaveWebService._get_form_str(form, "is_optional")
                == "true",
                is_compensatory=LeaveWebService._get_form_str(form, "is_compensatory")
                == "true",
                include_holidays=LeaveWebService._get_form_str(form, "include_holidays")
                == "true",
                applicable_after_days=int(applicable_after_days)
                if applicable_after_days
                else None,
                max_optional_leaves=int(max_optional_leaves)
                if max_optional_leaves
                else None,
                is_active=LeaveWebService._get_form_str(form, "is_active") == "true",
                description=LeaveWebService._get_form_str(form, "description") or None,
            )
            db.commit()
            return RedirectResponse("/people/leave/types", status_code=303)
        except Exception as e:
            logger.exception("Failed to update leave type: %s", e)
            db.rollback()
            context = base_context(request, auth, "Edit Leave Type", "leave", db=db)
            context["error"] = str(e)
            context["form_data"] = dict(form)
            try:
                context["leave_type"] = svc.get_leave_type(
                    org_id, coerce_uuid(leave_type_id)
                )
            except LeaveTypeNotFoundError:
                pass
            return templates.TemplateResponse(
                request, "people/leave/leave_type_form.html", context
            )

    def new_allocation_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """New leave allocation form."""
        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)
        context = base_context(request, auth, "New Leave Allocation", "leave", db=db)
        context["employees"] = self._get_employees(db, org_id)
        context["leave_types"] = svc.list_leave_types(org_id, is_active=True).items
        return templates.TemplateResponse(
            request, "people/leave/allocation_form.html", context
        )

    async def create_allocation_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle allocation creation."""
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)

        employee_id = LeaveWebService._get_form_str(form, "employee_id")
        leave_type_id = LeaveWebService._get_form_str(form, "leave_type_id")
        from_date_str = LeaveWebService._get_form_str(form, "from_date")
        to_date_str = LeaveWebService._get_form_str(form, "to_date")
        new_leaves = LeaveWebService._get_form_str(form, "new_leaves_allocated")

        if not all(
            [employee_id, leave_type_id, from_date_str, to_date_str, new_leaves]
        ):
            context = base_context(
                request, auth, "New Leave Allocation", "leave", db=db
            )
            context["error"] = "All required fields must be filled."
            context["form_data"] = dict(form)
            context["employees"] = self._get_employees(db, org_id)
            context["leave_types"] = svc.list_leave_types(org_id, is_active=True).items
            return templates.TemplateResponse(
                request, "people/leave/allocation_form.html", context
            )

        try:
            carry_forward = (
                LeaveWebService._get_form_str(form, "carry_forward_leaves") or "0"
            )
            svc.create_allocation(
                org_id,
                employee_id=coerce_uuid(employee_id),
                leave_type_id=coerce_uuid(leave_type_id),
                from_date=date.fromisoformat(from_date_str),
                to_date=date.fromisoformat(to_date_str),
                new_leaves_allocated=Decimal(new_leaves),
                carry_forward_leaves=Decimal(carry_forward),
                notes=LeaveWebService._get_form_str(form, "notes") or None,
            )
            db.commit()
            return RedirectResponse("/people/leave/allocations", status_code=303)
        except LeaveAllocationExistsError as e:
            db.rollback()
            context = base_context(
                request, auth, "New Leave Allocation", "leave", db=db
            )
            context["error"] = str(e)
            context["form_data"] = dict(form)
            context["employees"] = self._get_employees(db, org_id)
            context["leave_types"] = svc.list_leave_types(org_id, is_active=True).items
            return templates.TemplateResponse(
                request, "people/leave/allocation_form.html", context
            )
        except Exception as e:
            logger.exception("Failed to create leave allocation: %s", e)
            db.rollback()
            context = base_context(
                request, auth, "New Leave Allocation", "leave", db=db
            )
            context["error"] = str(e)
            context["form_data"] = dict(form)
            context["employees"] = self._get_employees(db, org_id)
            context["leave_types"] = svc.list_leave_types(org_id, is_active=True).items
            return templates.TemplateResponse(
                request, "people/leave/allocation_form.html", context
            )

    def view_allocation_response(
        self,
        request: Request,
        allocation_id: str,
        auth: WebAuthContext,
        db: Session,
        success: str | None = None,
        error: str | None = None,
    ) -> HTMLResponse | RedirectResponse:
        """View allocation details."""
        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)
        try:
            allocation = svc.get_allocation(org_id, coerce_uuid(allocation_id))
        except LeaveAllocationNotFoundError:
            return RedirectResponse("/people/leave/allocations", status_code=303)

        # Get related data
        employee = db.get(Employee, allocation.employee_id)
        try:
            leave_type = svc.get_leave_type(org_id, allocation.leave_type_id)
        except LeaveTypeNotFoundError:
            leave_type = None

        # Get related applications
        applications = svc.list_applications(
            org_id,
            employee_id=allocation.employee_id,
            leave_type_id=allocation.leave_type_id,
            from_date=allocation.from_date,
            to_date=allocation.to_date,
        ).items

        context = base_context(request, auth, "Leave Allocation", "leave", db=db)
        context["allocation"] = allocation
        context["employee"] = employee
        context["leave_type"] = leave_type
        context["applications"] = applications
        context["success"] = success
        context["error"] = error
        return templates.TemplateResponse(
            request, "people/leave/allocation_detail.html", context
        )

    def edit_allocation_form_response(
        self,
        request: Request,
        allocation_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Edit allocation form."""
        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)
        try:
            allocation = svc.get_allocation(org_id, coerce_uuid(allocation_id))
        except LeaveAllocationNotFoundError:
            return RedirectResponse("/people/leave/allocations", status_code=303)

        context = base_context(request, auth, "Edit Leave Allocation", "leave", db=db)
        context["allocation"] = allocation
        context["employees"] = self._get_employees(db, org_id)
        context["leave_types"] = svc.list_leave_types(org_id, is_active=True).items
        return templates.TemplateResponse(
            request, "people/leave/allocation_form.html", context
        )

    async def update_allocation_response(
        self,
        request: Request,
        allocation_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle allocation update."""
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)

        try:
            new_leaves = (
                LeaveWebService._get_form_str(form, "new_leaves_allocated") or "0"
            )
            carry_forward = (
                LeaveWebService._get_form_str(form, "carry_forward_leaves") or "0"
            )
            from_date_str = LeaveWebService._get_form_str(form, "from_date")
            to_date_str = LeaveWebService._get_form_str(form, "to_date")

            svc.update_allocation(
                org_id,
                coerce_uuid(allocation_id),
                from_date=date.fromisoformat(from_date_str) if from_date_str else None,
                to_date=date.fromisoformat(to_date_str) if to_date_str else None,
                new_leaves_allocated=Decimal(new_leaves),
                carry_forward_leaves=Decimal(carry_forward),
                notes=LeaveWebService._get_form_str(form, "notes") or None,
            )
            db.commit()
            return RedirectResponse(
                f"/people/leave/allocations/{allocation_id}", status_code=303
            )
        except Exception as e:
            logger.exception("Failed to update leave allocation: %s", e)
            db.rollback()
            context = base_context(
                request, auth, "Edit Leave Allocation", "leave", db=db
            )
            context["error"] = str(e)
            context["form_data"] = dict(form)
            try:
                context["allocation"] = svc.get_allocation(
                    org_id, coerce_uuid(allocation_id)
                )
            except LeaveAllocationNotFoundError:
                pass
            context["employees"] = self._get_employees(db, org_id)
            context["leave_types"] = svc.list_leave_types(org_id, is_active=True).items
            return templates.TemplateResponse(
                request, "people/leave/allocation_form.html", context
            )

    def delete_allocation_response(
        self,
        allocation_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Delete an allocation."""
        from urllib.parse import quote

        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)
        try:
            svc.delete_allocation(org_id, coerce_uuid(allocation_id))
            db.commit()
            success_msg = quote("Allocation deleted successfully")
            return RedirectResponse(
                url=f"/people/leave/allocations?success={success_msg}",
                status_code=303,
            )
        except LeaveServiceError:
            error_msg = quote("Unable to delete allocation")
            return RedirectResponse(
                url=f"/people/leave/allocations?error={error_msg}",
                status_code=303,
            )

    async def encash_allocation_response(
        self,
        request: Request,
        allocation_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Process leave encashment for an allocation."""
        from urllib.parse import quote

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        days_to_encash = LeaveWebService._get_form_str(form, "days_to_encash")
        notes = LeaveWebService._get_form_str(form, "encash_notes")

        org_id = coerce_uuid(auth.organization_id)
        alloc_id = coerce_uuid(allocation_id)
        svc = LeaveService(db, auth)

        try:
            allocation = svc.get_allocation(org_id, alloc_id)
            leave_type = svc.get_leave_type(org_id, allocation.leave_type_id)

            if not leave_type.allow_encashment:
                return RedirectResponse(
                    url=f"/people/leave/allocations/{allocation_id}?error=Encashment+not+allowed+for+this+leave+type",
                    status_code=303,
                )

            encash_days = Decimal(days_to_encash) if days_to_encash else Decimal("0")
            available = (
                allocation.total_leaves_allocated
                - allocation.leaves_used
                - allocation.leaves_encashed
                - allocation.leaves_expired
            )
            threshold = leave_type.encashment_threshold_days or Decimal("0")
            max_encashable = available - threshold

            if encash_days <= 0:
                return RedirectResponse(
                    url=f"/people/leave/allocations/{allocation_id}?error=Invalid+encashment+amount",
                    status_code=303,
                )

            if encash_days > max_encashable:
                return RedirectResponse(
                    url=f"/people/leave/allocations/{allocation_id}?error=Encashment+amount+exceeds+available+balance",
                    status_code=303,
                )

            # Update leaves_encashed
            new_encashed = allocation.leaves_encashed + encash_days
            new_notes = allocation.notes or ""
            if notes:
                encash_note = f"Encashed {encash_days} days on {date.today().isoformat()}: {notes}"
            else:
                encash_note = (
                    f"Encashed {encash_days} days on {date.today().isoformat()}"
                )

            if new_notes:
                new_notes = f"{new_notes}\n{encash_note}"
            else:
                new_notes = encash_note

            svc.update_allocation(
                org_id, alloc_id, leaves_encashed=new_encashed, notes=new_notes
            )
            db.commit()

            success_msg = quote(f"Successfully encashed {encash_days} days")
            return RedirectResponse(
                url=f"/people/leave/allocations/{allocation_id}?success={success_msg}",
                status_code=303,
            )

        except (LeaveServiceError, LeaveTypeNotFoundError) as e:
            error_msg = quote(str(e))
            return RedirectResponse(
                url=f"/people/leave/allocations/{allocation_id}?error={error_msg}",
                status_code=303,
            )

    async def bulk_create_allocations_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Bulk create leave allocations for multiple employees."""
        from urllib.parse import quote

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        employee_ids = form.getlist("employee_ids")
        leave_type_id = LeaveWebService._get_form_str(form, "leave_type_id")
        from_date_str = LeaveWebService._get_form_str(form, "from_date")
        to_date_str = LeaveWebService._get_form_str(form, "to_date")
        new_leaves = LeaveWebService._get_form_str(form, "new_leaves_allocated") or "0"
        carry_forward = (
            LeaveWebService._get_form_str(form, "carry_forward_leaves") or "0"
        )
        notes = LeaveWebService._get_form_str(form, "notes") or None

        if not employee_ids:
            return RedirectResponse(
                url="/people/leave/allocations?error=No+employees+selected",
                status_code=303,
            )

        if not leave_type_id or not from_date_str or not to_date_str:
            return RedirectResponse(
                url="/people/leave/allocations?error=Leave+type+and+dates+are+required",
                status_code=303,
            )

        valid_ids = []
        for emp_id in employee_ids:
            try:
                valid_ids.append(coerce_uuid(emp_id))
            except (ValueError, TypeError):
                logger.debug("Skipping invalid employee UUID: %s", emp_id)

        if not valid_ids:
            return RedirectResponse(
                url="/people/leave/allocations?error=No+valid+employees+selected",
                status_code=303,
            )

        try:
            org_id = coerce_uuid(auth.organization_id)
            svc = LeaveService(db, auth)

            result = svc.bulk_create_allocations(
                org_id,
                employee_ids=valid_ids,
                leave_type_id=coerce_uuid(leave_type_id),
                from_date=date.fromisoformat(from_date_str),
                to_date=date.fromisoformat(to_date_str),
                new_leaves_allocated=Decimal(new_leaves),
                carry_forward_leaves=Decimal(carry_forward),
                notes=notes,
            )
            db.commit()

            success_msg = quote(
                f"Created {result['success_count']} allocation(s). {result['failed_count']} failed."
            )
            return RedirectResponse(
                url=f"/people/leave/allocations?success={success_msg}", status_code=303
            )
        except Exception as e:
            logger.exception("Failed to bulk create leave allocations: %s", e)
            db.rollback()
            error_msg = quote(str(e))
            return RedirectResponse(
                url=f"/people/leave/allocations?error={error_msg}", status_code=303
            )

    def new_application_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """New leave application form."""
        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)
        context = base_context(request, auth, "New Leave Application", "leave", db=db)
        context["employees"] = self._get_employees(db, org_id)
        context["leave_types"] = svc.list_leave_types(org_id, is_active=True).items
        return templates.TemplateResponse(
            request, "people/leave/application_form.html", context
        )

    async def create_application_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle application creation."""
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)

        employee_id = LeaveWebService._get_form_str(form, "employee_id")
        leave_type_id = LeaveWebService._get_form_str(form, "leave_type_id")
        from_date_str = LeaveWebService._get_form_str(form, "from_date")
        to_date_str = LeaveWebService._get_form_str(form, "to_date")

        if not all([employee_id, leave_type_id, from_date_str, to_date_str]):
            context = base_context(
                request, auth, "New Leave Application", "leave", db=db
            )
            context["error"] = "Employee, leave type, and dates are required."
            context["form_data"] = dict(form)
            context["employees"] = self._get_employees(db, org_id)
            context["leave_types"] = svc.list_leave_types(org_id, is_active=True).items
            return templates.TemplateResponse(
                request, "people/leave/application_form.html", context
            )

        try:
            half_day = LeaveWebService._get_form_str(form, "half_day") == "true"
            half_day_date_str = LeaveWebService._get_form_str(form, "half_day_date")

            employee = db.get(Employee, coerce_uuid(employee_id))
            leave_approver_id = None
            if employee and employee.reports_to_id:
                leave_approver_id = employee.reports_to_id

            application = svc.create_application(
                org_id,
                employee_id=coerce_uuid(employee_id),
                leave_type_id=coerce_uuid(leave_type_id),
                from_date=date.fromisoformat(from_date_str),
                to_date=date.fromisoformat(to_date_str),
                half_day=half_day,
                half_day_date=date.fromisoformat(half_day_date_str)
                if half_day_date_str
                else None,
                reason=LeaveWebService._get_form_str(form, "reason") or None,
                leave_approver_id=leave_approver_id,
            )
            db.commit()
            return RedirectResponse(
                f"/people/leave/applications/{application.application_id}",
                status_code=303,
            )
        except InsufficientLeaveBalanceError as e:
            db.rollback()
            context = base_context(
                request, auth, "New Leave Application", "leave", db=db
            )
            context["error"] = (
                f"Insufficient leave balance. Available: {e.available}, Requested: {e.requested}"
            )
            context["form_data"] = dict(form)
            context["employees"] = self._get_employees(db, org_id)
            context["leave_types"] = svc.list_leave_types(org_id, is_active=True).items
            return templates.TemplateResponse(
                request, "people/leave/application_form.html", context
            )
        except Exception as e:
            logger.exception("Failed to create leave application: %s", e)
            db.rollback()
            context = base_context(
                request, auth, "New Leave Application", "leave", db=db
            )
            context["error"] = str(e)
            context["form_data"] = dict(form)
            context["employees"] = self._get_employees(db, org_id)
            context["leave_types"] = svc.list_leave_types(org_id, is_active=True).items
            return templates.TemplateResponse(
                request, "people/leave/application_form.html", context
            )

    def view_application_response(
        self,
        request: Request,
        application_id: str,
        success: str | None,
        error: str | None,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """View application details."""
        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)
        try:
            application = svc.get_application(org_id, coerce_uuid(application_id))
        except LeaveApplicationNotFoundError:
            return RedirectResponse("/people/leave/applications", status_code=303)

        # Get related data
        employee = db.get(Employee, application.employee_id)
        try:
            leave_type = svc.get_leave_type(org_id, application.leave_type_id)
        except LeaveTypeNotFoundError:
            leave_type = None

        approver = None
        if application.approved_by_id:
            approver = db.get(Person, application.approved_by_id)

        context = base_context(request, auth, "Leave Application", "leave", db=db)
        context["application"] = application
        context["employee"] = employee
        context["leave_type"] = leave_type
        context["approver"] = approver
        context["success"] = success
        context["error"] = error
        return templates.TemplateResponse(
            request, "people/leave/application_detail.html", context
        )

    def edit_application_form_response(
        self,
        request: Request,
        application_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Edit application form."""
        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)
        try:
            application = svc.get_application(org_id, coerce_uuid(application_id))
        except LeaveApplicationNotFoundError:
            return RedirectResponse("/people/leave/applications", status_code=303)

        context = base_context(request, auth, "Edit Leave Application", "leave", db=db)
        context["application"] = application
        context["employees"] = self._get_employees(db, org_id)
        context["leave_types"] = svc.list_leave_types(org_id, is_active=True).items
        return templates.TemplateResponse(
            request, "people/leave/application_form.html", context
        )

    async def update_application_response(
        self,
        request: Request,
        application_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle application update."""
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)

        try:
            from_date_str = LeaveWebService._get_form_str(form, "from_date")
            to_date_str = LeaveWebService._get_form_str(form, "to_date")
            half_day = LeaveWebService._get_form_str(form, "half_day") == "true"
            half_day_date_str = LeaveWebService._get_form_str(form, "half_day_date")

            svc.update_application(
                org_id,
                coerce_uuid(application_id),
                from_date=date.fromisoformat(from_date_str) if from_date_str else None,
                to_date=date.fromisoformat(to_date_str) if to_date_str else None,
                half_day=half_day,
                half_day_date=date.fromisoformat(half_day_date_str)
                if half_day_date_str
                else None,
                reason=LeaveWebService._get_form_str(form, "reason") or None,
            )
            db.commit()
            return RedirectResponse(
                f"/people/leave/applications/{application_id}", status_code=303
            )
        except Exception as e:
            logger.exception("Failed to update leave application: %s", e)
            db.rollback()
            context = base_context(
                request, auth, "Edit Leave Application", "leave", db=db
            )
            context["error"] = str(e)
            context["form_data"] = dict(form)
            try:
                context["application"] = svc.get_application(
                    org_id, coerce_uuid(application_id)
                )
            except LeaveApplicationNotFoundError:
                pass
            context["employees"] = self._get_employees(db, org_id)
            context["leave_types"] = svc.list_leave_types(org_id, is_active=True).items
            return templates.TemplateResponse(
                request, "people/leave/application_form.html", context
            )

    def approve_application_response(
        self,
        application_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Approve a leave application."""
        from urllib.parse import quote

        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)
        try:
            approver_id = coerce_uuid(auth.person_id) if auth.person_id else None
            svc.approve_application(
                org_id,
                coerce_uuid(application_id),
                approver_id=approver_id,
            )
            db.commit()
            success_msg = quote("Application approved")
            return RedirectResponse(
                f"/people/leave/applications/{application_id}?success={success_msg}",
                status_code=303,
            )
        except LeaveServiceError as exc:
            db.rollback()
            error_msg = quote(str(exc))
            return RedirectResponse(
                f"/people/leave/applications/{application_id}?error={error_msg}",
                status_code=303,
            )
        except Exception as exc:
            logger.exception("Failed to approve leave application %s: %s", application_id, exc)
            db.rollback()
            error_msg = quote(str(exc))
            return RedirectResponse(
                f"/people/leave/applications/{application_id}?error={error_msg}",
                status_code=303,
            )

    async def reject_application_response(
        self,
        request: Request,
        application_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Reject a leave application."""
        from urllib.parse import quote

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)
        try:
            approver_id = coerce_uuid(auth.person_id) if auth.person_id else None
            svc.reject_application(
                org_id,
                coerce_uuid(application_id),
                approver_id=approver_id,
                reason=LeaveWebService._get_form_str(form, "reason") or "Rejected",
            )
            db.commit()
            success_msg = quote("Application rejected")
            return RedirectResponse(
                f"/people/leave/applications/{application_id}?success={success_msg}",
                status_code=303,
            )
        except LeaveServiceError as exc:
            db.rollback()
            error_msg = quote(str(exc))
            return RedirectResponse(
                f"/people/leave/applications/{application_id}?error={error_msg}",
                status_code=303,
            )
        except Exception as exc:
            logger.exception("Failed to reject leave application %s: %s", application_id, exc)
            db.rollback()
            error_msg = quote(str(exc))
            return RedirectResponse(
                f"/people/leave/applications/{application_id}?error={error_msg}",
                status_code=303,
            )

    def cancel_application_response(
        self,
        application_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Cancel a leave application."""
        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)
        try:
            svc.cancel_application(org_id, coerce_uuid(application_id))
            db.commit()
        except LeaveServiceError:
            db.rollback()
        return RedirectResponse(
            f"/people/leave/applications/{application_id}", status_code=303
        )

    def new_holiday_list_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """New holiday list form."""
        context = base_context(request, auth, "New Holiday List", "leave", db=db)
        return templates.TemplateResponse(
            request, "people/leave/holiday_list_form.html", context
        )

    async def create_holiday_list_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle holiday list creation."""
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)

        list_code = LeaveWebService._get_form_str(form, "list_code")
        list_name = LeaveWebService._get_form_str(form, "list_name")
        from_date_str = LeaveWebService._get_form_str(form, "from_date")
        to_date_str = LeaveWebService._get_form_str(form, "to_date")

        if not all([list_code, list_name, from_date_str, to_date_str]):
            context = base_context(request, auth, "New Holiday List", "leave", db=db)
            context["error"] = "List code, name, and dates are required."
            context["form_data"] = dict(form)
            return templates.TemplateResponse(
                request, "people/leave/holiday_list_form.html", context
            )

        try:
            # Parse holidays from form
            holidays = []
            i = 0
            while True:
                holiday_date = LeaveWebService._get_form_str(
                    form, f"holidays[{i}][holiday_date]"
                )
                holiday_name = LeaveWebService._get_form_str(
                    form, f"holidays[{i}][holiday_name]"
                )
                if not holiday_date or not holiday_name:
                    break
                holidays.append(
                    {
                        "holiday_date": date.fromisoformat(holiday_date),
                        "holiday_name": holiday_name.strip(),
                        "is_optional": LeaveWebService._get_form_str(
                            form, f"holidays[{i}][is_optional]"
                        )
                        == "on",
                    }
                )
                i += 1

            from_date = date.fromisoformat(from_date_str)
            to_date = date.fromisoformat(to_date_str)
            svc.create_holiday_list(
                org_id,
                list_code=list_code,
                list_name=list_name,
                year=from_date.year,
                from_date=from_date,
                to_date=to_date,
                description=LeaveWebService._get_form_str(form, "description") or None,
                holidays=holidays if holidays else None,
            )
            db.commit()
            return RedirectResponse("/people/leave/holidays", status_code=303)
        except Exception as e:
            logger.exception("Failed to create holiday list: %s", e)
            db.rollback()
            context = base_context(request, auth, "New Holiday List", "leave", db=db)
            context["error"] = str(e)
            context["form_data"] = dict(form)
            return templates.TemplateResponse(
                request, "people/leave/holiday_list_form.html", context
            )

    def view_holiday_list_response(
        self,
        request: Request,
        holiday_list_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """View holiday list details."""
        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)
        try:
            holiday_list = svc.get_holiday_list(org_id, coerce_uuid(holiday_list_id))
        except HolidayListNotFoundError:
            return RedirectResponse("/people/leave/holidays", status_code=303)

        context = base_context(request, auth, "Holiday List", "leave", db=db)
        context["holiday_list"] = holiday_list
        return templates.TemplateResponse(
            request, "people/leave/holiday_list_form.html", context
        )

    def edit_holiday_list_form_response(
        self,
        request: Request,
        holiday_list_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Edit holiday list form."""
        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)
        try:
            holiday_list = svc.get_holiday_list(org_id, coerce_uuid(holiday_list_id))
        except HolidayListNotFoundError:
            return RedirectResponse("/people/leave/holidays", status_code=303)

        context = base_context(request, auth, "Edit Holiday List", "leave", db=db)
        context["holiday_list"] = holiday_list
        return templates.TemplateResponse(
            request, "people/leave/holiday_list_form.html", context
        )

    async def update_holiday_list_response(
        self,
        request: Request,
        holiday_list_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle holiday list update."""
        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)

        try:
            from_date_str = LeaveWebService._get_form_str(form, "from_date")
            to_date_str = LeaveWebService._get_form_str(form, "to_date")
            holidays = []
            i = 0
            while True:
                holiday_date = LeaveWebService._get_form_str(
                    form, f"holidays[{i}][holiday_date]"
                )
                holiday_name = LeaveWebService._get_form_str(
                    form, f"holidays[{i}][holiday_name]"
                )
                if not holiday_date or not holiday_name:
                    break
                holidays.append(
                    {
                        "holiday_date": date.fromisoformat(holiday_date),
                        "holiday_name": holiday_name.strip(),
                        "is_optional": LeaveWebService._get_form_str(
                            form, f"holidays[{i}][is_optional]"
                        )
                        == "on",
                    }
                )
                i += 1

            svc.update_holiday_list(
                org_id,
                coerce_uuid(holiday_list_id),
                list_name=LeaveWebService._get_form_str(form, "list_name"),
                from_date=date.fromisoformat(from_date_str) if from_date_str else None,
                to_date=date.fromisoformat(to_date_str) if to_date_str else None,
                description=LeaveWebService._get_form_str(form, "description") or None,
                is_active=LeaveWebService._get_form_str(form, "is_active") == "true",
                holidays=holidays,
            )
            db.commit()
            return RedirectResponse("/people/leave/holidays", status_code=303)
        except Exception as e:
            logger.exception("Failed to update holiday list: %s", e)
            db.rollback()
            context = base_context(request, auth, "Edit Holiday List", "leave", db=db)
            context["error"] = str(e)
            context["form_data"] = dict(form)
            try:
                context["holiday_list"] = svc.get_holiday_list(
                    org_id, coerce_uuid(holiday_list_id)
                )
            except HolidayListNotFoundError:
                pass
            return templates.TemplateResponse(
                request, "people/leave/holiday_list_form.html", context
            )

    def delete_holiday_list_response(
        self,
        holiday_list_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Delete a holiday list."""
        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)
        try:
            svc.delete_holiday_list(org_id, coerce_uuid(holiday_list_id))
            db.commit()
        except LeaveServiceError:
            pass
        return RedirectResponse("/people/leave/holidays", status_code=303)

    def leave_balance_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        year: int | None = None,
        department_id: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Leave balance report page."""
        from app.services.people.hr import DepartmentFilters, OrganizationService

        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)
        org_svc = OrganizationService(db, org_id)

        report = svc.get_leave_balance_report(
            org_id,
            year=year,
            department_id=self._parse_uuid(department_id),
        )

        departments = org_svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=200),
        ).items

        # Paginate employees list
        per_page = 50
        all_employees = report.get("employees", [])
        total_count = len(all_employees)
        total_pages = max(1, (total_count + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        start = (page - 1) * per_page
        report["employees"] = all_employees[start : start + per_page]

        active_filters = build_active_filters(
            params={
                "year": str(year) if year else None,
                "department_id": department_id,
            },
            labels={"year": "Year", "department_id": "Department"},
        )
        context = base_context(request, auth, "Leave Balance Report", "leave", db=db)
        context.update(
            {
                "report": report,
                "departments": departments,
                "year": year or date.today().year,
                "department_id": department_id,
                "page": page,
                "total_pages": total_pages,
                "total_count": total_count,
                "limit": per_page,
                "active_filters": active_filters,
            }
        )
        return templates.TemplateResponse(
            request, "people/leave/reports/balance.html", context
        )

    def leave_usage_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> HTMLResponse:
        """Leave usage report page."""
        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)

        report = svc.get_leave_usage_report(
            org_id,
            start_date=self._parse_date(start_date),
            end_date=self._parse_date(end_date),
        )

        context = base_context(request, auth, "Leave Usage Report", "leave", db=db)
        context.update(
            {
                "report": report,
                "start_date": start_date or report["start_date"].isoformat(),
                "end_date": end_date or report["end_date"].isoformat(),
            }
        )
        return templates.TemplateResponse(
            request, "people/leave/reports/usage.html", context
        )

    def leave_calendar_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
        department_id: str | None = None,
    ) -> HTMLResponse:
        """Leave calendar report page."""
        from app.services.people.hr import DepartmentFilters, OrganizationService

        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)
        org_svc = OrganizationService(db, org_id)

        report = svc.get_leave_calendar(
            org_id,
            start_date=self._parse_date(start_date),
            end_date=self._parse_date(end_date),
            department_id=self._parse_uuid(department_id),
        )

        departments = org_svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=200),
        ).items

        context = base_context(request, auth, "Leave Calendar", "leave", db=db)
        context.update(
            {
                "report": report,
                "departments": departments,
                "start_date": start_date or report["start_date"].isoformat(),
                "end_date": end_date or report["end_date"].isoformat(),
                "department_id": department_id,
            }
        )
        return templates.TemplateResponse(
            request, "people/leave/reports/calendar.html", context
        )

    def leave_trends_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        months: int = 12,
    ) -> HTMLResponse:
        """Leave trends report page."""
        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)

        report = svc.get_leave_trends_report(org_id, months=months)

        context = base_context(request, auth, "Leave Trends Report", "leave", db=db)
        context.update(
            {
                "report": report,
                "months": months,
            }
        )
        return templates.TemplateResponse(
            request, "people/leave/reports/trends.html", context
        )

    async def bulk_approve_applications_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Bulk approve leave applications."""
        from urllib.parse import quote

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        application_ids = form.getlist("application_ids")
        if not application_ids:
            return RedirectResponse(
                url="/people/leave/applications?error=No+applications+selected",
                status_code=303,
            )

        valid_ids = []
        for app_id in application_ids:
            try:
                valid_ids.append(coerce_uuid(app_id))
            except (ValueError, TypeError):
                logger.debug("Skipping invalid application UUID: %s", app_id)

        if not valid_ids:
            return RedirectResponse(
                url="/people/leave/applications?error=No+valid+applications+selected",
                status_code=303,
            )

        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)

        result = svc.bulk_approve_applications(
            org_id,
            application_ids=valid_ids,
            approver_id=coerce_uuid(auth.person_id) if auth.person_id else None,
        )
        db.commit()

        success_msg = quote(
            f"Successfully approved {result['updated']} of {result['requested']} application(s)"
        )
        return RedirectResponse(
            url=f"/people/leave/applications?success={success_msg}", status_code=303
        )

    async def bulk_reject_applications_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Bulk reject leave applications."""
        from urllib.parse import quote

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        application_ids = form.getlist("application_ids")
        rejection_reason = (
            LeaveWebService._get_form_str(form, "rejection_reason") or "Rejected"
        )

        if not application_ids:
            return RedirectResponse(
                url="/people/leave/applications?error=No+applications+selected",
                status_code=303,
            )

        valid_ids = []
        for app_id in application_ids:
            try:
                valid_ids.append(coerce_uuid(app_id))
            except (ValueError, TypeError):
                logger.debug("Skipping invalid application UUID: %s", app_id)

        if not valid_ids:
            return RedirectResponse(
                url="/people/leave/applications?error=No+valid+applications+selected",
                status_code=303,
            )

        org_id = coerce_uuid(auth.organization_id)
        svc = LeaveService(db, auth)

        result = svc.bulk_reject_applications(
            org_id,
            application_ids=valid_ids,
            approver_id=coerce_uuid(auth.person_id) if auth.person_id else None,
            reason=rejection_reason,
        )
        db.commit()

        success_msg = quote(
            f"Rejected {result['updated']} of {result['requested']} application(s)"
        )
        return RedirectResponse(
            url=f"/people/leave/applications?success={success_msg}", status_code=303
        )


leave_web_service = LeaveWebService()
