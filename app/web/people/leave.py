"""
Leave management web routes.

Provides list pages and CRUD forms for leave types, allocations, applications, and holiday lists.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.people.hr import Employee
from app.models.people.leave import LeaveApplicationStatus
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.leave import LeaveAllocationExistsError, LeaveService
from app.services.people.leave.leave_service import (
    LeaveServiceError,
    LeaveTypeNotFoundError,
    LeaveAllocationNotFoundError,
    LeaveApplicationNotFoundError,
    HolidayListNotFoundError,
    InsufficientLeaveBalanceError,
)
from app.templates import templates
from app.web.deps import WebAuthContext, base_context, get_db, require_hr_access


router = APIRouter(prefix="/leave", tags=["people-leave-web"])


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
def leave_overview(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Leave overview page."""
    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)
    stats = svc.get_leave_stats(org_id)
    context = base_context(request, auth, "Leave", "leave", db=db)
    context["stats"] = stats
    return templates.TemplateResponse(request, "people/leave/index.html", context)


@router.get("/types", response_class=HTMLResponse)
def leave_types(
    request: Request,
    search: Optional[str] = None,
    is_active: Optional[bool] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
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


@router.get("/applications", response_class=HTMLResponse)
def leave_applications(
    request: Request,
    employee_id: Optional[str] = None,
    leave_type_id: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
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
        employee_id=_parse_uuid(employee_id),
        leave_type_id=_parse_uuid(leave_type_id),
        status=status_enum,
        from_date=_parse_date(start_date),
        to_date=_parse_date(end_date),
        pagination=pagination,
    )
    context = base_context(request, auth, "Leave Applications", "leave", db=db)
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
        }
    )
    return templates.TemplateResponse(request, "people/leave/applications.html", context)


@router.get("/allocations", response_class=HTMLResponse)
def leave_allocations(
    request: Request,
    employee_id: Optional[str] = None,
    leave_type_id: Optional[str] = None,
    year: Optional[int] = None,
    is_active: Optional[bool] = None,
    page: int = Query(default=1, ge=1),
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Leave allocations list page."""
    org_id = coerce_uuid(auth.organization_id)
    pagination = PaginationParams.from_page(page, per_page=20)
    svc = LeaveService(db, auth)
    result = svc.list_allocations(
        org_id,
        employee_id=_parse_uuid(employee_id),
        leave_type_id=_parse_uuid(leave_type_id),
        year=year,
        is_active=is_active,
        pagination=pagination,
    )

    # Get data for bulk allocation dialog
    employees = _get_employees(db, org_id)
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
            "year": year,
            "is_active": is_active,
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
            "success": success,
            "error": error,
        }
    )
    return templates.TemplateResponse(request, "people/leave/allocations.html", context)


@router.get("/holidays", response_class=HTMLResponse)
def leave_holidays(
    request: Request,
    year: Optional[int] = None,
    is_active: Optional[bool] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
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
    context = base_context(request, auth, "Holiday Lists", "leave", db=db)
    context.update(
        {
            "lists": result.items,
            "year": year,
            "is_active": is_active,
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }
    )
    return templates.TemplateResponse(request, "people/leave/holidays.html", context)


# =============================================================================
# Leave Types CRUD
# =============================================================================


def _get_employees(db: Session, org_id: UUID) -> list:
    """Get active employees for dropdowns."""
    return list(
        db.scalars(
            select(Employee)
            .options(
                joinedload(Employee.person),
                joinedload(Employee.manager).joinedload(Employee.person),
            )
            .where(Employee.organization_id == org_id)
            .order_by(Employee.employee_code)
        ).all()
    )


@router.get("/types/new", response_class=HTMLResponse)
def new_leave_type_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New leave type form."""
    context = base_context(request, auth, "New Leave Type", "leave", db=db)
    return templates.TemplateResponse(request, "people/leave/leave_type_form.html", context)


@router.post("/types/new")
async def create_leave_type(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Handle leave type creation."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)

    leave_type_code = (form.get("leave_type_code") or "").strip()
    leave_type_name = (form.get("leave_type_name") or "").strip()

    if not leave_type_code or not leave_type_name:
        context = base_context(request, auth, "New Leave Type", "leave", db=db)
        context["error"] = "Leave type code and name are required."
        context["form_data"] = dict(form)
        return templates.TemplateResponse(request, "people/leave/leave_type_form.html", context)

    try:
        max_days = form.get("max_days_per_year")
        max_continuous = form.get("max_continuous_days")
        max_carry = form.get("max_carry_forward_days")
        carry_forward_expiry = form.get("carry_forward_expiry_months")
        encash_threshold = form.get("encashment_threshold_days")
        applicable_after_days = form.get("applicable_after_days")
        max_optional_leaves = form.get("max_optional_leaves")

        svc.create_leave_type(
            org_id,
            leave_type_code=leave_type_code,
            leave_type_name=leave_type_name,
            max_days_per_year=Decimal(max_days) if max_days else None,
            max_continuous_days=int(max_continuous) if max_continuous else None,
            allow_carry_forward=form.get("allow_carry_forward") == "true",
            max_carry_forward_days=Decimal(max_carry) if max_carry else None,
            carry_forward_expiry_months=int(carry_forward_expiry) if carry_forward_expiry else None,
            allow_encashment=form.get("allow_encashment") == "true",
            encashment_threshold_days=Decimal(encash_threshold) if encash_threshold else None,
            is_lwp=form.get("is_lwp") == "true",
            is_optional=form.get("is_optional") == "true",
            is_compensatory=form.get("is_compensatory") == "true",
            include_holidays=form.get("include_holidays") == "true",
            applicable_after_days=int(applicable_after_days) if applicable_after_days else 0,
            max_optional_leaves=int(max_optional_leaves) if max_optional_leaves else None,
            is_active=form.get("is_active") == "true",
            description=(form.get("description") or "").strip() or None,
        )
        db.commit()
        return RedirectResponse("/people/leave/types", status_code=303)
    except Exception as e:
        db.rollback()
        context = base_context(request, auth, "New Leave Type", "leave", db=db)
        context["error"] = str(e)
        context["form_data"] = dict(form)
        return templates.TemplateResponse(request, "people/leave/leave_type_form.html", context)


@router.get("/types/{leave_type_id}/edit", response_class=HTMLResponse)
def edit_leave_type_form(
    request: Request,
    leave_type_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit leave type form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)
    try:
        leave_type = svc.get_leave_type(org_id, coerce_uuid(leave_type_id))
    except LeaveTypeNotFoundError:
        return RedirectResponse("/people/leave/types", status_code=303)

    context = base_context(request, auth, "Edit Leave Type", "leave", db=db)
    context["leave_type"] = leave_type
    return templates.TemplateResponse(request, "people/leave/leave_type_form.html", context)


@router.post("/types/{leave_type_id}/edit")
async def update_leave_type(
    request: Request,
    leave_type_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Handle leave type update."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)

    try:
        max_days = form.get("max_days_per_year")
        max_continuous = form.get("max_continuous_days")
        max_carry = form.get("max_carry_forward_days")
        carry_forward_expiry = form.get("carry_forward_expiry_months")
        encash_threshold = form.get("encashment_threshold_days")
        applicable_after_days = form.get("applicable_after_days")
        max_optional_leaves = form.get("max_optional_leaves")

        svc.update_leave_type(
            org_id,
            coerce_uuid(leave_type_id),
            leave_type_name=(form.get("leave_type_name") or "").strip(),
            max_days_per_year=Decimal(max_days) if max_days else None,
            max_continuous_days=int(max_continuous) if max_continuous else None,
            allow_carry_forward=form.get("allow_carry_forward") == "true",
            max_carry_forward_days=Decimal(max_carry) if max_carry else None,
            carry_forward_expiry_months=int(carry_forward_expiry) if carry_forward_expiry else None,
            allow_encashment=form.get("allow_encashment") == "true",
            encashment_threshold_days=Decimal(encash_threshold) if encash_threshold else None,
            is_lwp=form.get("is_lwp") == "true",
            is_optional=form.get("is_optional") == "true",
            is_compensatory=form.get("is_compensatory") == "true",
            include_holidays=form.get("include_holidays") == "true",
            applicable_after_days=int(applicable_after_days) if applicable_after_days else None,
            max_optional_leaves=int(max_optional_leaves) if max_optional_leaves else None,
            is_active=form.get("is_active") == "true",
            description=(form.get("description") or "").strip() or None,
        )
        db.commit()
        return RedirectResponse("/people/leave/types", status_code=303)
    except Exception as e:
        db.rollback()
        context = base_context(request, auth, "Edit Leave Type", "leave", db=db)
        context["error"] = str(e)
        context["form_data"] = dict(form)
        try:
            context["leave_type"] = svc.get_leave_type(org_id, coerce_uuid(leave_type_id))
        except LeaveTypeNotFoundError:
            pass
        return templates.TemplateResponse(request, "people/leave/leave_type_form.html", context)


# =============================================================================
# Leave Allocations CRUD
# =============================================================================


@router.get("/allocations/new", response_class=HTMLResponse)
def new_allocation_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New leave allocation form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)
    context = base_context(request, auth, "New Leave Allocation", "leave", db=db)
    context["employees"] = _get_employees(db, org_id)
    context["leave_types"] = svc.list_leave_types(org_id, is_active=True).items
    return templates.TemplateResponse(request, "people/leave/allocation_form.html", context)


@router.post("/allocations/new")
async def create_allocation(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Handle allocation creation."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)

    employee_id = (form.get("employee_id") or "").strip()
    leave_type_id = (form.get("leave_type_id") or "").strip()
    from_date_str = (form.get("from_date") or "").strip()
    to_date_str = (form.get("to_date") or "").strip()
    new_leaves = (form.get("new_leaves_allocated") or "").strip()

    if not all([employee_id, leave_type_id, from_date_str, to_date_str, new_leaves]):
        context = base_context(request, auth, "New Leave Allocation", "leave", db=db)
        context["error"] = "All required fields must be filled."
        context["form_data"] = dict(form)
        context["employees"] = _get_employees(db, org_id)
        context["leave_types"] = svc.list_leave_types(org_id, is_active=True).items
        return templates.TemplateResponse(request, "people/leave/allocation_form.html", context)

    try:
        carry_forward = form.get("carry_forward_leaves") or "0"
        svc.create_allocation(
            org_id,
            employee_id=coerce_uuid(employee_id),
            leave_type_id=coerce_uuid(leave_type_id),
            from_date=date.fromisoformat(from_date_str),
            to_date=date.fromisoformat(to_date_str),
            new_leaves_allocated=Decimal(new_leaves),
            carry_forward_leaves=Decimal(carry_forward),
            notes=(form.get("notes") or "").strip() or None,
        )
        db.commit()
        return RedirectResponse("/people/leave/allocations", status_code=303)
    except LeaveAllocationExistsError as e:
        db.rollback()
        context = base_context(request, auth, "New Leave Allocation", "leave", db=db)
        context["error"] = str(e)
        context["form_data"] = dict(form)
        context["employees"] = _get_employees(db, org_id)
        context["leave_types"] = svc.list_leave_types(org_id, is_active=True).items
        return templates.TemplateResponse(request, "people/leave/allocation_form.html", context)
    except Exception as e:
        db.rollback()
        context = base_context(request, auth, "New Leave Allocation", "leave", db=db)
        context["error"] = str(e)
        context["form_data"] = dict(form)
        context["employees"] = _get_employees(db, org_id)
        context["leave_types"] = svc.list_leave_types(org_id, is_active=True).items
        return templates.TemplateResponse(request, "people/leave/allocation_form.html", context)


@router.get("/allocations/{allocation_id}", response_class=HTMLResponse)
def view_allocation(
    request: Request,
    allocation_id: str,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
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
    return templates.TemplateResponse(request, "people/leave/allocation_detail.html", context)


@router.get("/allocations/{allocation_id}/edit", response_class=HTMLResponse)
def edit_allocation_form(
    request: Request,
    allocation_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit allocation form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)
    try:
        allocation = svc.get_allocation(org_id, coerce_uuid(allocation_id))
    except LeaveAllocationNotFoundError:
        return RedirectResponse("/people/leave/allocations", status_code=303)

    context = base_context(request, auth, "Edit Leave Allocation", "leave", db=db)
    context["allocation"] = allocation
    context["employees"] = _get_employees(db, org_id)
    context["leave_types"] = svc.list_leave_types(org_id, is_active=True).items
    return templates.TemplateResponse(request, "people/leave/allocation_form.html", context)


@router.post("/allocations/{allocation_id}/edit")
async def update_allocation(
    request: Request,
    allocation_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Handle allocation update."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)

    try:
        new_leaves = form.get("new_leaves_allocated") or "0"
        carry_forward = form.get("carry_forward_leaves") or "0"
        from_date_str = form.get("from_date") or ""
        to_date_str = form.get("to_date") or ""

        svc.update_allocation(
            org_id,
            coerce_uuid(allocation_id),
            from_date=date.fromisoformat(from_date_str) if from_date_str else None,
            to_date=date.fromisoformat(to_date_str) if to_date_str else None,
            new_leaves_allocated=Decimal(new_leaves),
            carry_forward_leaves=Decimal(carry_forward),
            notes=(form.get("notes") or "").strip() or None,
        )
        db.commit()
        return RedirectResponse(f"/people/leave/allocations/{allocation_id}", status_code=303)
    except Exception as e:
        db.rollback()
        context = base_context(request, auth, "Edit Leave Allocation", "leave", db=db)
        context["error"] = str(e)
        context["form_data"] = dict(form)
        try:
            context["allocation"] = svc.get_allocation(org_id, coerce_uuid(allocation_id))
        except LeaveAllocationNotFoundError:
            pass
        context["employees"] = _get_employees(db, org_id)
        context["leave_types"] = svc.list_leave_types(org_id, is_active=True).items
        return templates.TemplateResponse(request, "people/leave/allocation_form.html", context)


@router.post("/allocations/{allocation_id}/delete")
async def delete_allocation(
    request: Request,
    allocation_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete an allocation."""
    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)
    try:
        svc.delete_allocation(org_id, coerce_uuid(allocation_id))
        db.commit()
    except LeaveServiceError:
        pass


@router.post("/allocations/{allocation_id}/encash")
async def encash_allocation(
    request: Request,
    allocation_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Process leave encashment for an allocation."""
    from urllib.parse import quote

    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    days_to_encash = (form.get("days_to_encash") or "").strip()
    notes = (form.get("encash_notes") or "").strip()

    org_id = coerce_uuid(auth.organization_id)
    alloc_id = coerce_uuid(allocation_id)
    svc = LeaveService(db, auth)

    try:
        allocation = svc.get_allocation(org_id, alloc_id)
        leave_type = svc.get_leave_type(org_id, allocation.leave_type_id)

        if not leave_type.allow_encashment:
            return RedirectResponse(
                url=f"/people/leave/allocations/{allocation_id}?error=Encashment+not+allowed+for+this+leave+type",
                status_code=303
            )

        encash_days = Decimal(days_to_encash) if days_to_encash else Decimal("0")
        available = (
            allocation.total_leaves_allocated
            - allocation.leaves_used
            - allocation.leaves_encashed
        )
        threshold = leave_type.encashment_threshold_days or Decimal("0")
        max_encashable = available - threshold

        if encash_days <= 0:
            return RedirectResponse(
                url=f"/people/leave/allocations/{allocation_id}?error=Invalid+encashment+amount",
                status_code=303
            )

        if encash_days > max_encashable:
            return RedirectResponse(
                url=f"/people/leave/allocations/{allocation_id}?error=Encashment+amount+exceeds+available+balance",
                status_code=303
            )

        # Update leaves_encashed
        new_encashed = allocation.leaves_encashed + encash_days
        new_notes = allocation.notes or ""
        if notes:
            encash_note = f"Encashed {encash_days} days on {date.today().isoformat()}: {notes}"
        else:
            encash_note = f"Encashed {encash_days} days on {date.today().isoformat()}"

        if new_notes:
            new_notes = f"{new_notes}\n{encash_note}"
        else:
            new_notes = encash_note

        svc.update_allocation(org_id, alloc_id, leaves_encashed=new_encashed, notes=new_notes)
        db.commit()

        success_msg = quote(f"Successfully encashed {encash_days} days")
        return RedirectResponse(
            url=f"/people/leave/allocations/{allocation_id}?success={success_msg}",
            status_code=303
        )

    except (LeaveServiceError, LeaveTypeNotFoundError) as e:
        error_msg = quote(str(e))
        return RedirectResponse(
            url=f"/people/leave/allocations/{allocation_id}?error={error_msg}",
            status_code=303
        )


@router.post("/allocations/bulk-create")
async def bulk_create_allocations(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Bulk create leave allocations for multiple employees."""
    from urllib.parse import quote

    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    employee_ids = form.getlist("employee_ids")
    leave_type_id = (form.get("leave_type_id") or "").strip()
    from_date_str = (form.get("from_date") or "").strip()
    to_date_str = (form.get("to_date") or "").strip()
    new_leaves = (form.get("new_leaves_allocated") or "0").strip()
    carry_forward = (form.get("carry_forward_leaves") or "0").strip()
    notes = (form.get("notes") or "").strip() or None

    if not employee_ids:
        return RedirectResponse(
            url="/people/leave/allocations?error=No+employees+selected",
            status_code=303
        )

    if not leave_type_id or not from_date_str or not to_date_str:
        return RedirectResponse(
            url="/people/leave/allocations?error=Leave+type+and+dates+are+required",
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
            url="/people/leave/allocations?error=No+valid+employees+selected",
            status_code=303
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

        success_msg = quote(f"Created {result['success_count']} allocation(s). {result['failed_count']} failed.")
        return RedirectResponse(url=f"/people/leave/allocations?success={success_msg}", status_code=303)
    except Exception as e:
        db.rollback()
        error_msg = quote(str(e))
        return RedirectResponse(url=f"/people/leave/allocations?error={error_msg}", status_code=303)
    return RedirectResponse("/people/leave/allocations", status_code=303)


# =============================================================================
# Leave Applications CRUD
# =============================================================================


@router.get("/applications/new", response_class=HTMLResponse)
def new_application_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New leave application form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)
    context = base_context(request, auth, "New Leave Application", "leave", db=db)
    context["employees"] = _get_employees(db, org_id)
    context["leave_types"] = svc.list_leave_types(org_id, is_active=True).items
    return templates.TemplateResponse(request, "people/leave/application_form.html", context)


@router.post("/applications/new")
async def create_application(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Handle application creation."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)

    employee_id = (form.get("employee_id") or "").strip()
    leave_type_id = (form.get("leave_type_id") or "").strip()
    from_date_str = (form.get("from_date") or "").strip()
    to_date_str = (form.get("to_date") or "").strip()

    if not all([employee_id, leave_type_id, from_date_str, to_date_str]):
        context = base_context(request, auth, "New Leave Application", "leave", db=db)
        context["error"] = "Employee, leave type, and dates are required."
        context["form_data"] = dict(form)
        context["employees"] = _get_employees(db, org_id)
        context["leave_types"] = svc.list_leave_types(org_id, is_active=True).items
        return templates.TemplateResponse(request, "people/leave/application_form.html", context)

    try:
        half_day = form.get("half_day") == "true"
        half_day_date_str = (form.get("half_day_date") or "").strip()

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
            half_day_date=date.fromisoformat(half_day_date_str) if half_day_date_str else None,
            reason=(form.get("reason") or "").strip() or None,
            leave_approver_id=leave_approver_id,
        )
        db.commit()
        return RedirectResponse(f"/people/leave/applications/{application.application_id}", status_code=303)
    except InsufficientLeaveBalanceError as e:
        db.rollback()
        context = base_context(request, auth, "New Leave Application", "leave", db=db)
        context["error"] = f"Insufficient leave balance. Available: {e.available}, Requested: {e.requested}"
        context["form_data"] = dict(form)
        context["employees"] = _get_employees(db, org_id)
        context["leave_types"] = svc.list_leave_types(org_id, is_active=True).items
        return templates.TemplateResponse(request, "people/leave/application_form.html", context)
    except Exception as e:
        db.rollback()
        context = base_context(request, auth, "New Leave Application", "leave", db=db)
        context["error"] = str(e)
        context["form_data"] = dict(form)
        context["employees"] = _get_employees(db, org_id)
        context["leave_types"] = svc.list_leave_types(org_id, is_active=True).items
        return templates.TemplateResponse(request, "people/leave/application_form.html", context)


@router.get("/applications/{application_id}", response_class=HTMLResponse)
def view_application(
    request: Request,
    application_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
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
        approver = db.get(Employee, application.approved_by_id)

    context = base_context(request, auth, "Leave Application", "leave", db=db)
    context["application"] = application
    context["employee"] = employee
    context["leave_type"] = leave_type
    context["approver"] = approver
    return templates.TemplateResponse(request, "people/leave/application_detail.html", context)


@router.get("/applications/{application_id}/edit", response_class=HTMLResponse)
def edit_application_form(
    request: Request,
    application_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit application form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)
    try:
        application = svc.get_application(org_id, coerce_uuid(application_id))
    except LeaveApplicationNotFoundError:
        return RedirectResponse("/people/leave/applications", status_code=303)

    context = base_context(request, auth, "Edit Leave Application", "leave", db=db)
    context["application"] = application
    context["employees"] = _get_employees(db, org_id)
    context["leave_types"] = svc.list_leave_types(org_id, is_active=True).items
    return templates.TemplateResponse(request, "people/leave/application_form.html", context)


@router.post("/applications/{application_id}/edit")
async def update_application(
    request: Request,
    application_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Handle application update."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)

    try:
        from_date_str = form.get("from_date") or ""
        to_date_str = form.get("to_date") or ""
        half_day = form.get("half_day") == "true"
        half_day_date_str = (form.get("half_day_date") or "").strip()

        svc.update_application(
            org_id,
            coerce_uuid(application_id),
            from_date=date.fromisoformat(from_date_str) if from_date_str else None,
            to_date=date.fromisoformat(to_date_str) if to_date_str else None,
            half_day=half_day,
            half_day_date=date.fromisoformat(half_day_date_str) if half_day_date_str else None,
            reason=(form.get("reason") or "").strip() or None,
        )
        db.commit()
        return RedirectResponse(f"/people/leave/applications/{application_id}", status_code=303)
    except Exception as e:
        db.rollback()
        context = base_context(request, auth, "Edit Leave Application", "leave", db=db)
        context["error"] = str(e)
        context["form_data"] = dict(form)
        try:
            context["application"] = svc.get_application(org_id, coerce_uuid(application_id))
        except LeaveApplicationNotFoundError:
            pass
        context["employees"] = _get_employees(db, org_id)
        context["leave_types"] = svc.list_leave_types(org_id, is_active=True).items
        return templates.TemplateResponse(request, "people/leave/application_form.html", context)


@router.post("/applications/{application_id}/approve")
async def approve_application(
    request: Request,
    application_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Approve a leave application."""
    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)
    try:
        # Get current user's employee ID for approver
        approver_id = None
        if auth.person_id:
            emp = db.scalar(
                select(Employee).where(
                    Employee.person_id == coerce_uuid(auth.person_id),
                    Employee.organization_id == org_id,
                )
            )
            if emp:
                approver_id = emp.employee_id

        svc.approve_application(
            org_id,
            coerce_uuid(application_id),
            approver_id=approver_id,
        )
        db.commit()
    except LeaveServiceError:
        db.rollback()
    return RedirectResponse(f"/people/leave/applications/{application_id}", status_code=303)


@router.post("/applications/{application_id}/reject")
async def reject_application(
    request: Request,
    application_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Reject a leave application."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)
    try:
        approver_id = None
        if auth.person_id:
            emp = db.scalar(
                select(Employee).where(
                    Employee.person_id == coerce_uuid(auth.person_id),
                    Employee.organization_id == org_id,
                )
            )
            if emp:
                approver_id = emp.employee_id

        svc.reject_application(
            org_id,
            coerce_uuid(application_id),
            approver_id=approver_id,
            reason=(form.get("reason") or "Rejected").strip(),
        )
        db.commit()
    except LeaveServiceError:
        db.rollback()
    return RedirectResponse(f"/people/leave/applications/{application_id}", status_code=303)


@router.post("/applications/{application_id}/cancel")
async def cancel_application(
    request: Request,
    application_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Cancel a leave application."""
    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)
    try:
        svc.cancel_application(org_id, coerce_uuid(application_id))
        db.commit()
    except LeaveServiceError:
        db.rollback()
    return RedirectResponse(f"/people/leave/applications/{application_id}", status_code=303)


# =============================================================================
# Holiday Lists CRUD
# =============================================================================


@router.get("/holidays/new", response_class=HTMLResponse)
def new_holiday_list_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New holiday list form."""
    context = base_context(request, auth, "New Holiday List", "leave", db=db)
    return templates.TemplateResponse(request, "people/leave/holiday_list_form.html", context)


@router.post("/holidays/new")
async def create_holiday_list(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Handle holiday list creation."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)

    list_code = (form.get("list_code") or "").strip()
    list_name = (form.get("list_name") or "").strip()
    from_date_str = (form.get("from_date") or "").strip()
    to_date_str = (form.get("to_date") or "").strip()

    if not all([list_code, list_name, from_date_str, to_date_str]):
        context = base_context(request, auth, "New Holiday List", "leave", db=db)
        context["error"] = "List code, name, and dates are required."
        context["form_data"] = dict(form)
        return templates.TemplateResponse(request, "people/leave/holiday_list_form.html", context)

    try:
        # Parse holidays from form
        holidays = []
        i = 0
        while True:
            holiday_date = form.get(f"holidays[{i}][holiday_date]")
            holiday_name = form.get(f"holidays[{i}][holiday_name]")
            if not holiday_date or not holiday_name:
                break
            holidays.append({
                "holiday_date": date.fromisoformat(holiday_date),
                "holiday_name": holiday_name.strip(),
                "is_optional": form.get(f"holidays[{i}][is_optional]") == "on",
            })
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
            description=(form.get("description") or "").strip() or None,
            holidays=holidays if holidays else None,
        )
        db.commit()
        return RedirectResponse("/people/leave/holidays", status_code=303)
    except Exception as e:
        db.rollback()
        context = base_context(request, auth, "New Holiday List", "leave", db=db)
        context["error"] = str(e)
        context["form_data"] = dict(form)
        return templates.TemplateResponse(request, "people/leave/holiday_list_form.html", context)


@router.get("/holidays/{holiday_list_id}", response_class=HTMLResponse)
def view_holiday_list(
    request: Request,
    holiday_list_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """View holiday list details."""
    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)
    try:
        holiday_list = svc.get_holiday_list(org_id, coerce_uuid(holiday_list_id))
    except HolidayListNotFoundError:
        return RedirectResponse("/people/leave/holidays", status_code=303)

    context = base_context(request, auth, "Holiday List", "leave", db=db)
    context["holiday_list"] = holiday_list
    return templates.TemplateResponse(request, "people/leave/holiday_list_form.html", context)


@router.get("/holidays/{holiday_list_id}/edit", response_class=HTMLResponse)
def edit_holiday_list_form(
    request: Request,
    holiday_list_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit holiday list form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)
    try:
        holiday_list = svc.get_holiday_list(org_id, coerce_uuid(holiday_list_id))
    except HolidayListNotFoundError:
        return RedirectResponse("/people/leave/holidays", status_code=303)

    context = base_context(request, auth, "Edit Holiday List", "leave", db=db)
    context["holiday_list"] = holiday_list
    return templates.TemplateResponse(request, "people/leave/holiday_list_form.html", context)


@router.post("/holidays/{holiday_list_id}/edit")
async def update_holiday_list(
    request: Request,
    holiday_list_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Handle holiday list update."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)

    try:
        from_date_str = form.get("from_date") or ""
        to_date_str = form.get("to_date") or ""
        holidays = []
        i = 0
        while True:
            holiday_date = form.get(f"holidays[{i}][holiday_date]")
            holiday_name = form.get(f"holidays[{i}][holiday_name]")
            if not holiday_date or not holiday_name:
                break
            holidays.append({
                "holiday_date": date.fromisoformat(holiday_date),
                "holiday_name": holiday_name.strip(),
                "is_optional": form.get(f"holidays[{i}][is_optional]") == "on",
            })
            i += 1

        svc.update_holiday_list(
            org_id,
            coerce_uuid(holiday_list_id),
            list_name=(form.get("list_name") or "").strip(),
            from_date=date.fromisoformat(from_date_str) if from_date_str else None,
            to_date=date.fromisoformat(to_date_str) if to_date_str else None,
            description=(form.get("description") or "").strip() or None,
            is_active=form.get("is_active") == "true",
            holidays=holidays,
        )
        db.commit()
        return RedirectResponse("/people/leave/holidays", status_code=303)
    except Exception as e:
        db.rollback()
        context = base_context(request, auth, "Edit Holiday List", "leave", db=db)
        context["error"] = str(e)
        context["form_data"] = dict(form)
        try:
            context["holiday_list"] = svc.get_holiday_list(org_id, coerce_uuid(holiday_list_id))
        except HolidayListNotFoundError:
            pass
        return templates.TemplateResponse(request, "people/leave/holiday_list_form.html", context)


@router.post("/holidays/{holiday_list_id}/delete")
async def delete_holiday_list(
    request: Request,
    holiday_list_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete a holiday list."""
    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)
    try:
        svc.delete_holiday_list(org_id, coerce_uuid(holiday_list_id))
        db.commit()
    except LeaveServiceError:
        pass
    return RedirectResponse("/people/leave/holidays", status_code=303)


# =============================================================================
# Reports
# =============================================================================


@router.get("/reports/balance", response_class=HTMLResponse)
def leave_balance_report(
    request: Request,
    year: Optional[int] = None,
    department_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Leave balance report page."""
    from app.services.people.hr import OrganizationService, DepartmentFilters

    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)
    org_svc = OrganizationService(db, org_id)

    report = svc.get_leave_balance_report(
        org_id,
        year=year,
        department_id=_parse_uuid(department_id),
    )

    departments = org_svc.list_departments(
        DepartmentFilters(is_active=True),
        PaginationParams(limit=200),
    ).items

    context = base_context(request, auth, "Leave Balance Report", "leave", db=db)
    context.update({
        "report": report,
        "departments": departments,
        "year": year or date.today().year,
        "department_id": department_id,
    })
    return templates.TemplateResponse(request, "people/leave/reports/balance.html", context)


@router.get("/reports/usage", response_class=HTMLResponse)
def leave_usage_report(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Leave usage report page."""
    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)

    report = svc.get_leave_usage_report(
        org_id,
        start_date=_parse_date(start_date),
        end_date=_parse_date(end_date),
    )

    context = base_context(request, auth, "Leave Usage Report", "leave", db=db)
    context.update({
        "report": report,
        "start_date": start_date or report["start_date"].isoformat(),
        "end_date": end_date or report["end_date"].isoformat(),
    })
    return templates.TemplateResponse(request, "people/leave/reports/usage.html", context)


@router.get("/reports/calendar", response_class=HTMLResponse)
def leave_calendar_report(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    department_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Leave calendar report page."""
    from app.services.people.hr import OrganizationService, DepartmentFilters

    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)
    org_svc = OrganizationService(db, org_id)

    report = svc.get_leave_calendar(
        org_id,
        start_date=_parse_date(start_date),
        end_date=_parse_date(end_date),
        department_id=_parse_uuid(department_id),
    )

    departments = org_svc.list_departments(
        DepartmentFilters(is_active=True),
        PaginationParams(limit=200),
    ).items

    context = base_context(request, auth, "Leave Calendar", "leave", db=db)
    context.update({
        "report": report,
        "departments": departments,
        "start_date": start_date or report["start_date"].isoformat(),
        "end_date": end_date or report["end_date"].isoformat(),
        "department_id": department_id,
    })
    return templates.TemplateResponse(request, "people/leave/reports/calendar.html", context)


@router.get("/reports/trends", response_class=HTMLResponse)
def leave_trends_report(
    request: Request,
    months: int = Query(default=12, ge=3, le=24),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Leave trends report page."""
    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)

    report = svc.get_leave_trends_report(org_id, months=months)

    context = base_context(request, auth, "Leave Trends Report", "leave", db=db)
    context.update({
        "report": report,
        "months": months,
    })
    return templates.TemplateResponse(request, "people/leave/reports/trends.html", context)


# =============================================================================
# Bulk Operations
# =============================================================================


@router.post("/applications/bulk-approve")
async def bulk_approve_applications(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Bulk approve leave applications."""
    from urllib.parse import quote

    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    application_ids = form.getlist("application_ids")
    if not application_ids:
        return RedirectResponse(
            url="/people/leave/applications?error=No+applications+selected",
            status_code=303
        )

    valid_ids = []
    for app_id in application_ids:
        try:
            valid_ids.append(coerce_uuid(app_id))
        except Exception:
            pass

    if not valid_ids:
        return RedirectResponse(
            url="/people/leave/applications?error=No+valid+applications+selected",
            status_code=303
        )

    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)

    result = svc.bulk_approve_applications(
        org_id,
        application_ids=valid_ids,
        approver_id=coerce_uuid(auth.user_id) if auth.user_id else None,
    )
    db.commit()

    success_msg = quote(f"Successfully approved {result['updated']} of {result['requested']} application(s)")
    return RedirectResponse(url=f"/people/leave/applications?success={success_msg}", status_code=303)


@router.post("/applications/bulk-reject")
async def bulk_reject_applications(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Bulk reject leave applications."""
    from urllib.parse import quote

    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    application_ids = form.getlist("application_ids")
    rejection_reason = (form.get("rejection_reason") or "").strip() or "Rejected"

    if not application_ids:
        return RedirectResponse(
            url="/people/leave/applications?error=No+applications+selected",
            status_code=303
        )

    valid_ids = []
    for app_id in application_ids:
        try:
            valid_ids.append(coerce_uuid(app_id))
        except Exception:
            pass

    if not valid_ids:
        return RedirectResponse(
            url="/people/leave/applications?error=No+valid+applications+selected",
            status_code=303
        )

    org_id = coerce_uuid(auth.organization_id)
    svc = LeaveService(db, auth)

    result = svc.bulk_reject_applications(
        org_id,
        application_ids=valid_ids,
        approver_id=coerce_uuid(auth.user_id) if auth.user_id else None,
        reason=rejection_reason,
    )
    db.commit()

    success_msg = quote(f"Rejected {result['updated']} of {result['requested']} application(s)")
    return RedirectResponse(url=f"/people/leave/applications?success={success_msg}", status_code=303)
