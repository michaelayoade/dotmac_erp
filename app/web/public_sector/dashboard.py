"""
Public Sector – Dashboard web route.

Thin wrapper that delegates to IPSASWebService for dashboard context.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.finance.ipsas.web.ipsas_web import IPSASWebService
from app.templates import templates
from app.web.deps import (
    WebAuthContext,
    base_context,
    get_db,
    require_public_sector_access,
)

router = APIRouter(tags=["public-sector-dashboard"])


@router.get("/", response_class=HTMLResponse)
def public_sector_dashboard(
    request: Request,
    auth: WebAuthContext = Depends(require_public_sector_access),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Public sector module dashboard."""
    context = base_context(request, auth, "Dashboard", "ps_dashboard", db=db)
    web_svc = IPSASWebService(db)
    context.update(web_svc.available_balance_dashboard_context(auth.organization_id))
    return templates.TemplateResponse(request, "public_sector/dashboard.html", context)
