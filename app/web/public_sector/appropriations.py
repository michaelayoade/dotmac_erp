"""
Public Sector – Appropriation web routes.

Thin wrappers that delegate to IPSASWebService and AppropriationService.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.finance.ipsas.web.ipsas_web import IPSASWebService
from app.services.finance.platform.org_context import org_context_service
from app.templates import templates
from app.web.deps import (
    WebAuthContext,
    base_context,
    get_db,
    require_public_sector_access,
)

router = APIRouter(tags=["public-sector-appropriations"])


@router.get("/appropriations", response_class=HTMLResponse)
def list_appropriations(
    request: Request,
    fiscal_year_id: str | None = None,
    fund_id: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_public_sector_access),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Appropriation list page."""
    context = base_context(request, auth, "Appropriations", "ps_funds", db=db)
    web_svc = IPSASWebService(db)
    context.update(
        web_svc.appropriation_list_context(
            auth.organization_id,
            fiscal_year_id=UUID(fiscal_year_id) if fiscal_year_id else None,
            fund_id=UUID(fund_id) if fund_id else None,
            status=status,
            page=page,
        )
    )
    return templates.TemplateResponse(
        request, "public_sector/appropriation_list.html", context
    )


@router.get("/appropriations/new", response_class=HTMLResponse)
def new_appropriation_form(
    request: Request,
    auth: WebAuthContext = Depends(require_public_sector_access),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Create appropriation form page."""
    from sqlalchemy import select

    from app.models.finance.gl.fiscal_year import FiscalYear
    from app.models.finance.ipsas.fund import Fund

    context = base_context(request, auth, "New Appropriation", "ps_funds", db=db)
    funds = list(
        db.scalars(
            select(Fund)
            .where(Fund.organization_id == auth.organization_id)
            .order_by(Fund.fund_code)
        ).all()
    )
    fiscal_years = list(
        db.scalars(
            select(FiscalYear)
            .where(FiscalYear.organization_id == auth.organization_id)
            .order_by(FiscalYear.start_date.desc())
        ).all()
    )
    context["funds"] = funds
    context["fiscal_years"] = fiscal_years
    return templates.TemplateResponse(
        request, "public_sector/appropriation_form.html", context
    )


@router.post("/appropriations/new")
def create_appropriation(
    request: Request,
    fiscal_year_id: str = Form(...),
    fund_id: str = Form(...),
    appropriation_code: str = Form(...),
    appropriation_name: str = Form(...),
    appropriation_type: str = Form(...),
    approved_amount: str = Form(...),
    currency_code: str | None = Form(None),
    effective_from: str = Form(...),
    budget_id: str | None = Form(None),
    account_id: str | None = Form(None),
    cost_center_id: str | None = Form(None),
    business_unit_id: str | None = Form(None),
    appropriation_act_reference: str | None = Form(None),
    auth: WebAuthContext = Depends(require_public_sector_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Create an appropriation (form submission)."""
    from datetime import date as date_type

    from app.schemas.finance.ipsas import AppropriationCreate
    from app.services.finance.ipsas.appropriation_service import AppropriationService

    data = AppropriationCreate(
        fiscal_year_id=UUID(fiscal_year_id),
        fund_id=UUID(fund_id),
        appropriation_code=appropriation_code,
        appropriation_name=appropriation_name,
        appropriation_type=appropriation_type,
        approved_amount=Decimal(approved_amount),
        currency_code=currency_code
        or org_context_service.get_functional_currency(db, auth.organization_id),
        effective_from=date_type.fromisoformat(effective_from),
        budget_id=UUID(budget_id) if budget_id else None,
        account_id=UUID(account_id) if account_id else None,
        cost_center_id=UUID(cost_center_id) if cost_center_id else None,
        business_unit_id=UUID(business_unit_id) if business_unit_id else None,
        appropriation_act_reference=appropriation_act_reference or None,
    )
    svc = AppropriationService(db)
    approp = svc.create(auth.organization_id, data, auth.user_id)
    return RedirectResponse(
        f"/public-sector/appropriations/{approp.appropriation_id}", status_code=303
    )


@router.get("/appropriations/{appropriation_id}", response_class=HTMLResponse)
def view_appropriation(
    request: Request,
    appropriation_id: str,
    auth: WebAuthContext = Depends(require_public_sector_access),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Appropriation detail page."""
    context = base_context(request, auth, "Appropriation Detail", "ps_funds", db=db)
    web_svc = IPSASWebService(db)
    context.update(
        web_svc.appropriation_detail_context(
            auth.organization_id, UUID(appropriation_id)
        )
    )
    return templates.TemplateResponse(
        request, "public_sector/appropriation_detail.html", context
    )


@router.post("/appropriations/{appropriation_id}/approve")
def approve_appropriation(
    request: Request,
    appropriation_id: str,
    auth: WebAuthContext = Depends(require_public_sector_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Approve an appropriation (web form submission)."""
    from app.services.finance.ipsas.appropriation_service import AppropriationService

    svc = AppropriationService(db)
    svc.get_or_404(UUID(appropriation_id), auth.organization_id)
    svc.approve(UUID(appropriation_id), auth.user_id)
    return RedirectResponse(
        f"/public-sector/appropriations/{appropriation_id}", status_code=303
    )
