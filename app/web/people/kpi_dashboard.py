"""Configurable KPI dashboard under People > Performance.

The existing private/hybrid performance mode and People access gates remain in
force. Department authorization is additionally enforced by the dashboard service.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.people.perf.web.kpi_dashboard_web import kpi_dashboard_web_service
from app.web.deps import (
    WebAuthContext,
    get_db_for_org,
    require_hr_access,
    require_private_performance_mode,
)

router = APIRouter(
    prefix="/perf/kpi-dashboard",
    tags=["people-kpi-dashboard"],
    dependencies=[Depends(require_private_performance_mode)],
)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def kpi_dashboard(
    request: Request,
    page: int = Query(default=1, ge=1, le=100_000),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    return kpi_dashboard_web_service.dashboard_response(request, auth, db, page=page)


@router.post("/views")
async def save_kpi_dashboard_view(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    response = await kpi_dashboard_web_service.save_response(request, auth, db)
    db.commit()
    return response


@router.post("/views/{view_id}/delete")
def delete_kpi_dashboard_view(
    request: Request,
    view_id: UUID,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db_for_org),
):
    response = kpi_dashboard_web_service.delete_response(auth, db, view_id)
    db.commit()
    return response
