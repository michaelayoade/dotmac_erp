"""
Public Sector – Commitment web routes.

Thin wrappers that delegate to IPSASWebService and CommitmentService.
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

router = APIRouter(tags=["public-sector-commitments"])


@router.get("/commitments", response_class=HTMLResponse)
def list_commitments(
    request: Request,
    fund_id: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_public_sector_access),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Commitment register page."""
    context = base_context(
        request, auth, "Commitment Register", "ps_commitments", db=db
    )
    web_svc = IPSASWebService(db)
    context.update(
        web_svc.commitment_list_context(
            auth.organization_id,
            fund_id=UUID(fund_id) if fund_id else None,
            status=status,
            page=page,
        )
    )
    return templates.TemplateResponse(
        request, "public_sector/commitment_list.html", context
    )


@router.get("/commitments/new", response_class=HTMLResponse)
def new_commitment_form(
    request: Request,
    auth: WebAuthContext = Depends(require_public_sector_access),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Create commitment form page."""
    from sqlalchemy import select

    from app.models.finance.gl.account import Account
    from app.models.finance.gl.fiscal_period import FiscalPeriod
    from app.models.finance.gl.fiscal_year import FiscalYear
    from app.models.finance.ipsas.appropriation import Appropriation
    from app.models.finance.ipsas.fund import Fund

    context = base_context(request, auth, "New Commitment", "ps_commitments", db=db)
    org_id = auth.organization_id

    context["funds"] = list(
        db.scalars(
            select(Fund).where(Fund.organization_id == org_id).order_by(Fund.fund_code)
        ).all()
    )
    context["appropriations"] = list(
        db.scalars(
            select(Appropriation)
            .where(Appropriation.organization_id == org_id)
            .order_by(Appropriation.appropriation_code)
        ).all()
    )
    context["accounts"] = list(
        db.scalars(
            select(Account)
            .where(Account.organization_id == org_id, Account.is_active.is_(True))
            .order_by(Account.account_code)
        ).all()
    )
    context["fiscal_years"] = list(
        db.scalars(
            select(FiscalYear)
            .where(FiscalYear.organization_id == org_id)
            .order_by(FiscalYear.start_date.desc())
        ).all()
    )
    context["fiscal_periods"] = list(
        db.scalars(
            select(FiscalPeriod)
            .where(FiscalPeriod.organization_id == org_id)
            .order_by(FiscalPeriod.start_date.desc())
        ).all()
    )
    context["commitment_types"] = [
        {"value": "PURCHASE_ORDER", "label": "Purchase Order"},
        {"value": "CONTRACT", "label": "Contract"},
        {"value": "PAYROLL", "label": "Payroll"},
        {"value": "OTHER", "label": "Other"},
    ]

    return templates.TemplateResponse(
        request, "public_sector/commitment_form.html", context
    )


@router.post("/commitments/new")
def create_commitment(
    request: Request,
    commitment_number: str = Form(...),
    commitment_type: str = Form(...),
    fund_id: str = Form(...),
    account_id: str = Form(...),
    fiscal_year_id: str = Form(...),
    fiscal_period_id: str = Form(...),
    committed_amount: str = Form(...),
    currency_code: str | None = Form(None),
    appropriation_id: str | None = Form(None),
    auth: WebAuthContext = Depends(require_public_sector_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Create a commitment (form submission)."""
    from app.services.finance.ipsas.commitment_service import CommitmentService

    svc = CommitmentService(db)
    commitment = svc.create(
        organization_id=auth.organization_id,
        commitment_number=commitment_number,
        commitment_type=commitment_type,
        fund_id=UUID(fund_id),
        account_id=UUID(account_id),
        fiscal_year_id=UUID(fiscal_year_id),
        fiscal_period_id=UUID(fiscal_period_id),
        committed_amount=Decimal(committed_amount),
        currency_code=currency_code
        or org_context_service.get_functional_currency(db, auth.organization_id),
        created_by_user_id=auth.user_id,
        appropriation_id=UUID(appropriation_id) if appropriation_id else None,
    )
    return RedirectResponse(
        f"/public-sector/commitments/{commitment.commitment_id}", status_code=303
    )


@router.get("/commitments/{commitment_id}", response_class=HTMLResponse)
def view_commitment(
    request: Request,
    commitment_id: str,
    auth: WebAuthContext = Depends(require_public_sector_access),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Commitment detail page."""
    context = base_context(request, auth, "Commitment Detail", "ps_commitments", db=db)
    web_svc = IPSASWebService(db)
    context.update(
        web_svc.commitment_detail_context(auth.organization_id, UUID(commitment_id))
    )
    return templates.TemplateResponse(
        request, "public_sector/commitment_detail.html", context
    )
