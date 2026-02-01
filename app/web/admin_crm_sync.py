"""
DotMac CRM Sync Admin web routes.

Provides UI for managing CRM sync integration.
"""
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.admin.crm_sync_web import crm_sync_web_service
from app.web.deps import get_db, optional_web_auth, WebAuthContext


router = APIRouter(prefix="/admin/sync/crm", tags=["admin-crm-sync-web"])


def _normalize_form(form: Any) -> dict[str, str]:
    if form is None:
        return {}
    return {key: value if isinstance(value, str) else "" for key, value in form.items()}


# ============ Dashboard ============


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def crm_sync_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """CRM sync management dashboard."""
    return crm_sync_web_service.dashboard_response(request, db, auth)


# ============ Configuration ============


@router.get("/config", response_class=HTMLResponse)
def crm_sync_config(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """CRM sync configuration page."""
    return crm_sync_web_service.config_response(request, db, auth)


@router.post("/config", response_class=HTMLResponse)
async def crm_sync_config_save(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Save CRM sync configuration."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()
    form = _normalize_form(form)

    is_active = form.get("is_active") == "on"
    sync_projects = form.get("sync_projects") == "on"
    sync_tickets = form.get("sync_tickets") == "on"
    sync_work_orders = form.get("sync_work_orders") == "on"

    return crm_sync_web_service.config_save_response(
        request,
        db,
        auth,
        is_active=is_active,
        sync_projects=sync_projects,
        sync_tickets=sync_tickets,
        sync_work_orders=sync_work_orders,
    )


@router.post("/config/generate-key", response_class=HTMLResponse)
def crm_sync_generate_key(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
) -> RedirectResponse:
    """Generate a new API key for CRM integration."""
    return crm_sync_web_service.generate_api_key_response(request, db, auth)


@router.post("/config/revoke-key", response_class=HTMLResponse)
def crm_sync_revoke_key(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
) -> RedirectResponse:
    """Revoke the CRM service API key."""
    return crm_sync_web_service.revoke_api_key_response(request, db, auth)


# ============ Entities ============


@router.get("/entities", response_class=HTMLResponse)
def crm_sync_entities(
    request: Request,
    entity_type: str = Query(default=""),
    status: str = Query(default=""),
    search: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """List synced CRM entities."""
    return crm_sync_web_service.entities_response(
        request,
        db,
        auth,
        entity_type=entity_type or None,
        status=status or None,
        search=search or None,
        page=page,
    )
