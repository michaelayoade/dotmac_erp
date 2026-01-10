"""
FA (Fixed Assets) Web Routes.

HTML template routes for Assets and Depreciation.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.web.deps import get_db, require_web_auth, WebAuthContext, base_context
from app.services.ifrs.fa.web import fa_web_service

templates = Jinja2Templates(directory="templates")

router = APIRouter(prefix="/fa", tags=["fa-web"])


# =============================================================================
# Assets
# =============================================================================

@router.get("/assets", response_class=HTMLResponse)
def list_assets(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    search: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Assets list page."""
    context = base_context(request, auth, "Fixed Assets", "fa")
    context.update(
        fa_web_service.list_assets_context(
            db,
            str(auth.organization_id),
            search=search,
            category=category,
            status=status,
            page=page,
        )
    )
    return templates.TemplateResponse(request, "ifrs/fa/assets.html", context)


# =============================================================================
# Depreciation
# =============================================================================

@router.get("/depreciation", response_class=HTMLResponse)
def depreciation_schedule(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    asset_id: Optional[str] = None,
    period: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Depreciation schedule page."""
    context = base_context(request, auth, "Depreciation Schedule", "fa")
    context.update(
        fa_web_service.depreciation_context(
            db,
            str(auth.organization_id),
            asset_id=asset_id,
            period=period,
        )
    )
    return templates.TemplateResponse(request, "ifrs/fa/depreciation.html", context)
