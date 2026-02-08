"""
Admin Sync Management web routes.

Provides UI for managing ERPNext sync operations.
"""

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.admin.sync_web import sync_web_service
from app.web.deps import WebAuthContext, get_db, optional_web_auth

router = APIRouter(prefix="/admin/sync", tags=["admin-sync-web"])


def _normalize_form(form: Any) -> dict[str, str]:
    if form is None:
        return {}
    return {key: value if isinstance(value, str) else "" for key, value in form.items()}


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def sync_dashboard(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Sync management dashboard."""
    return sync_web_service.dashboard_response(request, db, auth)


@router.get("/history", response_class=HTMLResponse)
def sync_history(
    request: Request,
    page: int = Query(default=1, ge=1),
    status: str = Query(default=""),
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Sync history list."""
    return sync_web_service.history_response(request, db, auth, page, status)


@router.get("/history/{history_id}", response_class=HTMLResponse)
def sync_history_detail(
    request: Request,
    history_id: str,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Sync history detail."""
    return sync_web_service.history_detail_response(request, db, auth, history_id)


@router.post("/trigger")
async def trigger_sync(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Trigger a sync operation."""
    raw_form = await request.form()
    form = _normalize_form(raw_form)
    sync_type = form.get("sync_type", "incremental")
    entity_types_raw = raw_form.getlist("entity_types")
    entity_types = [e for e in entity_types_raw if e] if entity_types_raw else None

    return sync_web_service.trigger_sync_response(
        request, db, auth, sync_type, entity_types
    )


@router.get("/config", response_class=HTMLResponse)
def sync_config(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """ERPNext integration configuration."""
    return sync_web_service.config_response(request, db, auth)


@router.post("/config")
async def save_sync_config(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Save ERPNext integration configuration."""
    raw_form = await request.form()
    form = _normalize_form(raw_form)
    base_url = (form.get("base_url") or "").strip()
    api_key = (form.get("api_key") or "").strip()
    api_secret = (form.get("api_secret") or "").strip()
    company = (form.get("company") or "").strip()
    is_active = form.get("is_active") == "on"

    return sync_web_service.save_config_response(
        request, db, auth, base_url, api_key, api_secret, company, is_active
    )


@router.get("/entities", response_class=HTMLResponse)
def sync_entities(
    request: Request,
    doctype: str = Query(default=""),
    status: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """View synced entities."""
    return sync_web_service.entities_response(request, db, auth, doctype, status, page)


@router.post("/config/test")
def test_connection(
    request: Request,
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """Test ERPNext connection."""
    return sync_web_service.test_connection_response(request, db, auth)
