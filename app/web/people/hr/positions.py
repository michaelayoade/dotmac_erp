"""Position management routes."""

from types import SimpleNamespace
from typing import Any
from urllib.parse import quote_plus
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.people.hr import PositionAssignmentType
from app.services.common import PaginationParams, ServiceError, coerce_uuid
from app.services.common_filters import build_active_filters
from app.services.people.hr import (
    DepartmentFilters,
    DesignationFilters,
    OrganizationService,
    PositionAssignmentCreateData,
    PositionCreateData,
    PositionService,
    PositionUpdateData,
)
from app.services.people.hr.web.employee_web import DEFAULT_PAGE_SIZE, DROPDOWN_LIMIT
from app.templates import templates
from app.web.deps import WebAuthContext, base_context, get_db, require_hr_access

from ._common import _parse_bool

router = APIRouter(tags=["positions"])


def _form_str(form: Any, key: str) -> str:
    value = form.get(key)
    if value is None:
        return ""
    return str(value).strip()


def _form_date(form: Any, key: str):
    value = _form_str(form, key)
    if not value:
        return None
    from datetime import date

    return date.fromisoformat(value)


def _form_uuid(form: Any, key: str) -> UUID | None:
    value = _form_str(form, key)
    if not value:
        return None
    result: UUID | None = coerce_uuid(value, raise_http=False)
    return result


def _position_payload(form: Any) -> PositionCreateData:
    designation_id = _form_str(form, "designation_id")
    department_id = _form_str(form, "department_id")
    parent_position_id = _form_str(form, "parent_position_id")
    return PositionCreateData(
        designation_id=coerce_uuid(designation_id) if designation_id else None,
        department_id=coerce_uuid(department_id) if department_id else None,
        parent_position_id=coerce_uuid(parent_position_id)
        if parent_position_id
        else None,
        is_vacant=_parse_bool(form.get("is_vacant")),
    )


def _position_form_context(
    request: Request,
    auth: WebAuthContext,
    db: Session,
    *,
    title: str,
    position: Any = None,
    errors: dict[str, str] | None = None,
    success: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    org_id = coerce_uuid(auth.organization_id)
    org_svc = OrganizationService(db, org_id)
    position_svc = PositionService(db, org_id)
    position_id = getattr(position, "position_id", None)
    is_edit = position_id is not None
    return {
        **base_context(request, auth, title, "positions", db=db),
        "position": position,
        "departments": org_svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=DROPDOWN_LIMIT),
        ).items,
        "designations": org_svc.list_designations(
            DesignationFilters(is_active=True),
            PaginationParams(limit=DROPDOWN_LIMIT),
        ).items,
        "parent_positions": position_svc.list_parent_options(
            exclude_position_id=position_id,
        ),
        "assignments": position_svc.list_assignments(position_id) if is_edit else [],
        "employee_options": position_svc.list_employee_options() if is_edit else [],
        "assignment_types": list(PositionAssignmentType),
        "errors": errors or {},
        "success": success,
        "error": error,
    }


@router.get("/positions", response_class=HTMLResponse)
def list_positions(
    request: Request,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    success: str | None = None,
    error: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Position list page."""
    org_id = coerce_uuid(auth.organization_id)
    pagination = PaginationParams.from_page(page, DEFAULT_PAGE_SIZE)
    result = PositionService(db, org_id).list_position_summaries(
        search=search,
        pagination=pagination,
    )
    context = {
        **base_context(request, auth, "Positions", "positions", db=db),
        "positions": result.items,
        "search": search or "",
        "page": page,
        "limit": result.limit,
        "total_count": result.total,
        "total_pages": result.total_pages,
        "success": success,
        "error": error,
        "active_filters": build_active_filters(
            params={"search": search},
            labels={"search": "Search"},
        ),
    }
    return templates.TemplateResponse(request, "people/hr/positions.html", context)


@router.get("/positions/tree", response_class=HTMLResponse)
def positions_tree(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Organizational chart view rendered as a recursive tree."""
    org_id = coerce_uuid(auth.organization_id)
    roots = PositionService(db, org_id).build_org_chart()
    context = {
        **base_context(request, auth, "Organization Chart", "positions", db=db),
        "roots": roots,
    }
    return templates.TemplateResponse(request, "people/hr/positions_tree.html", context)


@router.get("/positions/new", response_class=HTMLResponse)
def new_position_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New position form."""
    context = _position_form_context(
        request,
        auth,
        db,
        title="New Position",
    )
    return templates.TemplateResponse(request, "people/hr/position_form.html", context)


@router.post("/positions/new")
async def create_position(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a position."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    payload = _position_payload(form)
    if not payload.designation_id and not payload.department_id:
        position = SimpleNamespace(**payload.__dict__)
        context = _position_form_context(
            request,
            auth,
            db,
            title="New Position",
            position=position,
            errors={
                "designation_id": "Select a designation or department",
                "department_id": "Select a designation or department",
            },
            error="Select at least a designation or department.",
        )
        return templates.TemplateResponse(
            request,
            "people/hr/position_form.html",
            context,
        )

    try:
        PositionService(db, coerce_uuid(auth.organization_id)).create_position(payload)
        db.commit()
    except ServiceError as exc:
        db.rollback()
        position = SimpleNamespace(**payload.__dict__)
        context = _position_form_context(
            request,
            auth,
            db,
            title="New Position",
            position=position,
            error=exc.message,
        )
        return templates.TemplateResponse(
            request,
            "people/hr/position_form.html",
            context,
        )

    return RedirectResponse(
        url="/people/hr/positions?success=Position+created", status_code=303
    )


@router.get("/positions/{position_id}/edit", response_class=HTMLResponse)
def edit_position_form(
    request: Request,
    position_id: UUID,
    success: str | None = None,
    error: str | None = None,
    form_error: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit position form."""
    try:
        position = PositionService(db, coerce_uuid(auth.organization_id)).get_position(
            position_id
        )
    except ServiceError:
        return RedirectResponse(
            url="/people/hr/positions?error=Position+not+found",
            status_code=303,
        )
    context = _position_form_context(
        request,
        auth,
        db,
        title="Edit Position",
        position=position,
        success=success,
        error=form_error or error,
    )
    return templates.TemplateResponse(request, "people/hr/position_form.html", context)


@router.post("/positions/{position_id}/edit")
async def update_position(
    request: Request,
    position_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update a position."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    create_payload = _position_payload(form)
    payload = PositionUpdateData(**create_payload.__dict__)
    if not payload.designation_id and not payload.department_id:
        position = SimpleNamespace(position_id=position_id, **payload.__dict__)
        context = _position_form_context(
            request,
            auth,
            db,
            title="Edit Position",
            position=position,
            errors={
                "designation_id": "Select a designation or department",
                "department_id": "Select a designation or department",
            },
            error="Select at least a designation or department.",
        )
        return templates.TemplateResponse(
            request,
            "people/hr/position_form.html",
            context,
        )

    try:
        PositionService(db, coerce_uuid(auth.organization_id)).update_position(
            position_id,
            payload,
        )
        db.commit()
    except ServiceError as exc:
        db.rollback()
        position = SimpleNamespace(position_id=position_id, **payload.__dict__)
        context = _position_form_context(
            request,
            auth,
            db,
            title="Edit Position",
            position=position,
            error=exc.message,
        )
        return templates.TemplateResponse(
            request,
            "people/hr/position_form.html",
            context,
        )

    return RedirectResponse(
        url="/people/hr/positions?success=Position+saved", status_code=303
    )


@router.post("/positions/{position_id}/assignments")
async def create_position_assignment(
    request: Request,
    position_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Assign an employee to a position."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    try:
        start_date = _form_date(form, "start_date")
        if not start_date:
            raise ServiceError("Start date is required")
        employee_id = _form_uuid(form, "employee_id")
        if not employee_id:
            raise ServiceError("Select an employee")
        assignment_type_value = _form_str(form, "assignment_type")
        if not assignment_type_value:
            raise ServiceError("Select an assignment type")
        payload = PositionAssignmentCreateData(
            employee_id=employee_id,
            assignment_type=PositionAssignmentType(assignment_type_value),
            start_date=start_date,
            end_date=_form_date(form, "end_date"),
        )
        PositionService(db, coerce_uuid(auth.organization_id)).create_assignment(
            position_id,
            payload,
        )
        db.commit()
    except (ServiceError, ValueError) as exc:
        db.rollback()
        message = exc.message if isinstance(exc, ServiceError) else "Invalid assignment"
        return RedirectResponse(
            url=f"/people/hr/positions/{position_id}/edit?form_error={quote_plus(message)}",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/people/hr/positions/{position_id}/edit?success=Assignment+saved",
        status_code=303,
    )


@router.post("/positions/{position_id}/assignments/{assignment_id}/end")
async def end_position_assignment(
    request: Request,
    position_id: UUID,
    assignment_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """End a position assignment."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    try:
        end_date = _form_date(form, "end_date")
        if not end_date:
            raise ServiceError("End date is required")
        PositionService(db, coerce_uuid(auth.organization_id)).end_assignment(
            position_id,
            assignment_id,
            end_date=end_date,
        )
        db.commit()
    except (ServiceError, ValueError) as exc:
        db.rollback()
        message = exc.message if isinstance(exc, ServiceError) else "Invalid end date"
        return RedirectResponse(
            url=f"/people/hr/positions/{position_id}/edit?form_error={quote_plus(message)}",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/people/hr/positions/{position_id}/edit?success=Assignment+ended",
        status_code=303,
    )
