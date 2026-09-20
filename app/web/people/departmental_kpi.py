"""Thin HTML routes for configurable departmental KPI management."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.people.perf.web.departmental_kpi_web import (
    departmental_kpi_web_service,
)
from app.web.deps import (
    WebAuthContext,
    get_db_for_org,
    require_any_web_permission,
    require_private_performance_mode,
)

READ_PERMISSIONS = [
    "performance:kpi:dashboard:view",
    "performance:kpi:dashboard:view_all_departments",
    "performance:kpi:manage",
]

require_kpi_read = require_any_web_permission(READ_PERMISSIONS)
require_kpi_manage = require_any_web_permission(["performance:kpi:manage"])
require_kpi_measure = require_any_web_permission(
    ["performance:kpi:measure", "performance:kpi:approve"]
)
require_kpi_approve = require_any_web_permission(["performance:kpi:approve"])
require_kpi_export = require_any_web_permission(["performance:kpi:export"])

router = APIRouter(
    prefix="/perf/kpi-dashboard",
    tags=["departmental-kpi"],
    dependencies=[Depends(require_private_performance_mode)],
)


def _semantic_target(path: str, request: Request) -> RedirectResponse:
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(url=f"{path}{query}", status_code=307)


@router.get("/definitions")
def definitions_alias(
    request: Request,
    auth: WebAuthContext = Depends(require_kpi_read),
) -> RedirectResponse:
    """Semantic KPI Definitions entry point preserving the legacy handler URL."""
    return _semantic_target("/people/perf/kpi-dashboard/configurations", request)


@router.get("/assignments")
def assignments_alias(
    request: Request,
    auth: WebAuthContext = Depends(require_kpi_read),
) -> RedirectResponse:
    """Semantic KPI Assignments entry point preserving the legacy handler URL."""
    return _semantic_target("/people/perf/goals", request)


@router.get("/scorecards")
def scorecards_alias(
    request: Request,
    auth: WebAuthContext = Depends(require_kpi_read),
) -> RedirectResponse:
    """Semantic Department Scorecards entry point preserving the legacy handler URL."""
    return _semantic_target("/people/perf/kpi-dashboard/department", request)


@router.get("/department", response_class=HTMLResponse)
def dashboard(
    request: Request,
    department_id: UUID | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    employee_id: UUID | None = None,
    category: str | None = None,
    status: str | None = None,
    auth: WebAuthContext = Depends(require_kpi_read),
    db: Session = Depends(get_db_for_org),
):
    return departmental_kpi_web_service.dashboard_response(
        request,
        auth,
        db,
        department_id=department_id,
        period_start=period_start,
        period_end=period_end,
        employee_id=employee_id,
        category=category,
        status=status,
    )


@router.get("/export.csv")
def export_dashboard(
    department_id: UUID,
    period_start: date | None = None,
    period_end: date | None = None,
    employee_id: UUID | None = None,
    category: str | None = None,
    status: str | None = None,
    auth: WebAuthContext = Depends(require_kpi_export),
    db: Session = Depends(get_db_for_org),
):
    return departmental_kpi_web_service.export_dashboard_response(
        auth,
        db,
        department_id=department_id,
        period_start=period_start,
        period_end=period_end,
        employee_id=employee_id,
        category=category,
        status=status,
    )


@router.post("/periods")
async def instantiate_period(
    request: Request,
    auth: WebAuthContext = Depends(require_kpi_manage),
    db: Session = Depends(get_db_for_org),
):
    return await departmental_kpi_web_service.instantiate_period_response(
        request, auth, db
    )


@router.get("/configurations", response_class=HTMLResponse)
def configurations(
    request: Request,
    department_id: str | None = None,
    include_inactive: bool = False,
    auth: WebAuthContext = Depends(require_kpi_manage),
    db: Session = Depends(get_db_for_org),
):
    return departmental_kpi_web_service.configuration_list_response(
        request,
        auth,
        db,
        department_id=department_id,
        include_inactive=include_inactive,
    )


@router.get("/configurations/new", response_class=HTMLResponse)
def new_configuration(
    request: Request,
    auth: WebAuthContext = Depends(require_kpi_manage),
    db: Session = Depends(get_db_for_org),
):
    return departmental_kpi_web_service.configuration_form_response(request, auth, db)


@router.post("/configurations/new", response_class=HTMLResponse)
async def create_configuration(
    request: Request,
    auth: WebAuthContext = Depends(require_kpi_manage),
    db: Session = Depends(get_db_for_org),
):
    return await departmental_kpi_web_service.save_configuration_response(
        request, auth, db
    )


@router.get("/configurations/{template_id}", response_class=HTMLResponse)
def configuration_detail(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_kpi_read),
    db: Session = Depends(get_db_for_org),
):
    return departmental_kpi_web_service.configuration_detail_response(
        request, auth, db, template_id=template_id
    )


@router.get("/configurations/{template_id}/edit", response_class=HTMLResponse)
def edit_configuration(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_kpi_manage),
    db: Session = Depends(get_db_for_org),
):
    return departmental_kpi_web_service.configuration_form_response(
        request, auth, db, template_id=template_id
    )


@router.post("/configurations/{template_id}/edit", response_class=HTMLResponse)
async def update_configuration(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_kpi_manage),
    db: Session = Depends(get_db_for_org),
):
    return await departmental_kpi_web_service.save_configuration_response(
        request, auth, db, template_id=template_id
    )


@router.post("/configurations/{template_id}/refresh")
async def refresh_configuration(
    request: Request,
    template_id: str,
    auth: WebAuthContext = Depends(require_kpi_measure),
    db: Session = Depends(get_db_for_org),
):
    return await departmental_kpi_web_service.refresh_automatic_form_response(
        request,
        auth,
        db,
        template_id=template_id,
    )


@router.get("/measurements", response_class=HTMLResponse)
def measurements(
    request: Request,
    period_start: date | None = None,
    period_end: date | None = None,
    state: str = "all",
    employee_search: str = "",
    page: int = Query(default=1, ge=1, le=100_000),
    auth: WebAuthContext = Depends(require_kpi_read),
    db: Session = Depends(get_db_for_org),
):
    return departmental_kpi_web_service.measurement_queue_response(
        request,
        auth,
        db,
        period_start=period_start,
        period_end=period_end,
        state=state,
        employee_search=employee_search,
        page=page,
    )


@router.get("/measurements/{kpi_id}", response_class=HTMLResponse)
def measurement_form(
    request: Request,
    kpi_id: str,
    auth: WebAuthContext = Depends(require_kpi_measure),
    db: Session = Depends(get_db_for_org),
):
    return departmental_kpi_web_service.measurement_form_response(
        request, auth, db, kpi_id=kpi_id
    )


@router.post("/measurements/{kpi_id}", response_class=HTMLResponse)
async def save_measurement(
    request: Request,
    kpi_id: str,
    auth: WebAuthContext = Depends(require_kpi_measure),
    db: Session = Depends(get_db_for_org),
):
    return await departmental_kpi_web_service.save_measurement_response(
        request, auth, db, kpi_id=kpi_id
    )


@router.post("/measurement-history/{history_id}/approve")
def approve_measurement(
    history_id: str,
    auth: WebAuthContext = Depends(require_kpi_approve),
    db: Session = Depends(get_db_for_org),
):
    return departmental_kpi_web_service.approve_measurement_response(
        auth, db, history_id=history_id
    )


@router.get("/employees/{employee_id}", response_class=HTMLResponse)
def employee_dashboard(
    request: Request,
    employee_id: str,
    period_start: str | None = None,
    period_end: str | None = None,
    auth: WebAuthContext = Depends(require_kpi_read),
    db: Session = Depends(get_db_for_org),
):
    return departmental_kpi_web_service.employee_dashboard_response(
        request,
        auth,
        db,
        employee_id=employee_id,
        period_start=period_start,
        period_end=period_end,
    )
