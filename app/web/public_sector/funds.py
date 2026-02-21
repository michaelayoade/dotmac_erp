"""
Public Sector – Fund web routes.

Thin wrappers that delegate to IPSASWebService and FundService.
"""

from __future__ import annotations

from datetime import date as date_type
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.finance.ipsas.web.ipsas_web import IPSASWebService
from app.templates import templates
from app.web.deps import (
    WebAuthContext,
    base_context,
    get_db,
    require_public_sector_access,
)

router = APIRouter(tags=["public-sector-funds"])


@router.get("/funds", response_class=HTMLResponse)
def list_funds(
    request: Request,
    status: str | None = None,
    fund_type: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_public_sector_access),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Fund list page."""
    context = base_context(request, auth, "Funds", "ps_funds", db=db)
    web_svc = IPSASWebService(db)
    context.update(
        web_svc.fund_list_context(auth.organization_id, status, fund_type, page)
    )
    return templates.TemplateResponse(request, "public_sector/fund_list.html", context)


@router.get("/funds/new", response_class=HTMLResponse)
def new_fund_form(
    request: Request,
    auth: WebAuthContext = Depends(require_public_sector_access),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Create fund form page."""
    context = base_context(request, auth, "New Fund", "ps_funds", db=db)
    web_svc = IPSASWebService(db)
    context.update(web_svc.fund_form_context(auth.organization_id))
    return templates.TemplateResponse(request, "public_sector/fund_form.html", context)


@router.get("/funds/{fund_id}", response_class=HTMLResponse)
def view_fund(
    request: Request,
    fund_id: str,
    auth: WebAuthContext = Depends(require_public_sector_access),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Fund detail page."""
    context = base_context(request, auth, "Fund Detail", "ps_funds", db=db)
    web_svc = IPSASWebService(db)
    context.update(web_svc.fund_detail_context(auth.organization_id, UUID(fund_id)))
    return templates.TemplateResponse(
        request, "public_sector/fund_detail.html", context
    )


@router.post("/funds/new")
def create_fund(
    request: Request,
    fund_code: str = Form(...),
    fund_name: str = Form(...),
    fund_type: str = Form(...),
    effective_from: str = Form(...),
    description: str = Form(""),
    is_restricted: str | None = Form(None),
    donor_name: str | None = Form(None),
    donor_reference: str | None = Form(None),
    auth: WebAuthContext = Depends(require_public_sector_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Create a fund (form submission)."""
    from app.schemas.finance.ipsas import FundCreate
    from app.services.finance.ipsas.fund_service import FundService

    data = FundCreate(
        fund_code=fund_code,
        fund_name=fund_name,
        fund_type=fund_type,
        effective_from=date_type.fromisoformat(effective_from),
        description=description or None,
        is_restricted=is_restricted is not None,
        donor_name=donor_name,
        donor_reference=donor_reference,
    )
    svc = FundService(db)
    fund = svc.create(auth.organization_id, data, auth.user_id)
    return RedirectResponse(f"/public-sector/funds/{fund.fund_id}", status_code=303)
