"""
FIN_INST (Financial Instruments) Web Routes.

HTML template routes for Instruments and Hedges.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.web.deps import get_db, require_web_auth, WebAuthContext, base_context
from app.services.ifrs.fin_inst.web import fin_inst_web_service

templates = Jinja2Templates(directory="templates")

router = APIRouter(prefix="/fin-inst", tags=["fin-inst-web"])


# =============================================================================
# Instruments
# =============================================================================

@router.get("/instruments", response_class=HTMLResponse)
def list_instruments(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    search: Optional[str] = None,
    instrument_type: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Financial instruments list page."""
    context = base_context(request, auth, "Financial Instruments", "fin_inst")
    context.update(
        fin_inst_web_service.list_instruments_context(
            db,
            str(auth.organization_id),
            search=search,
            instrument_type=instrument_type,
            status=status,
            page=page,
        )
    )
    return templates.TemplateResponse(request, "ifrs/fin_inst/instruments.html", context)


# =============================================================================
# Hedges
# =============================================================================

@router.get("/hedges", response_class=HTMLResponse)
def list_hedges(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    search: Optional[str] = None,
    hedge_type: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Hedge relationships list page."""
    context = base_context(request, auth, "Hedge Accounting", "fin_inst")
    context.update(
        fin_inst_web_service.list_hedges_context(
            db,
            str(auth.organization_id),
            search=search,
            hedge_type=hedge_type,
            status=status,
            page=page,
        )
    )
    return templates.TemplateResponse(request, "ifrs/fin_inst/hedges.html", context)
