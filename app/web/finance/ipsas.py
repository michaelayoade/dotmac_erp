"""
IPSAS Web Routes.

HTML template routes for IPSAS Fund Accounting, Appropriations,
Commitments, Virements, and Budget Comparison.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.finance.ipsas.web.ipsas_web import IPSASWebService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context, get_db, require_finance_access

router = APIRouter(prefix="/ipsas", tags=["ipsas-web"])


# =============================================================================
# Funds
# =============================================================================


@router.get("/funds", response_class=HTMLResponse)
def list_funds(
    request: Request,
    status: Optional[str] = None,
    fund_type: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Fund list page."""
    context = base_context(request, auth, "Funds", "ipsas", db=db)
    web_svc = IPSASWebService(db)
    context.update(
        web_svc.fund_list_context(auth.organization_id, status, fund_type, page)
    )
    return templates.TemplateResponse(request, "finance/ipsas/fund_list.html", context)


@router.get("/funds/new", response_class=HTMLResponse)
def new_fund_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Create fund form page."""
    context = base_context(request, auth, "New Fund", "ipsas", db=db)
    web_svc = IPSASWebService(db)
    context.update(web_svc.fund_form_context(auth.organization_id))
    return templates.TemplateResponse(request, "finance/ipsas/fund_form.html", context)


@router.get("/funds/{fund_id}", response_class=HTMLResponse)
def view_fund(
    request: Request,
    fund_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Fund detail page."""
    context = base_context(request, auth, "Fund Detail", "ipsas", db=db)
    web_svc = IPSASWebService(db)
    context.update(web_svc.fund_detail_context(UUID(fund_id)))
    return templates.TemplateResponse(
        request, "finance/ipsas/fund_detail.html", context
    )


@router.post("/funds/new")
def create_fund(
    request: Request,
    fund_code: str = Form(...),
    fund_name: str = Form(...),
    fund_type: str = Form(...),
    effective_from: str = Form(...),
    description: str = Form(""),
    is_restricted: Optional[str] = Form(None),
    donor_name: Optional[str] = Form(None),
    donor_reference: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Create a fund (form submission)."""
    from datetime import date as date_type

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
    db.commit()
    return RedirectResponse(f"/finance/ipsas/funds/{fund.fund_id}", status_code=303)


# =============================================================================
# Appropriations
# =============================================================================


@router.get("/appropriations", response_class=HTMLResponse)
def list_appropriations(
    request: Request,
    fiscal_year_id: Optional[str] = None,
    fund_id: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Appropriation list page."""
    context = base_context(request, auth, "Appropriations", "ipsas", db=db)
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
        request, "finance/ipsas/appropriation_list.html", context
    )


@router.get("/appropriations/new", response_class=HTMLResponse)
def new_appropriation_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Create appropriation form page."""
    from app.models.finance.gl.fiscal_year import FiscalYear
    from app.models.finance.ipsas.fund import Fund
    from sqlalchemy import select

    context = base_context(request, auth, "New Appropriation", "ipsas", db=db)
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
        request, "finance/ipsas/appropriation_form.html", context
    )


@router.get("/appropriations/{appropriation_id}", response_class=HTMLResponse)
def view_appropriation(
    request: Request,
    appropriation_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Appropriation detail page."""
    context = base_context(request, auth, "Appropriation Detail", "ipsas", db=db)
    web_svc = IPSASWebService(db)
    context.update(web_svc.appropriation_detail_context(UUID(appropriation_id)))
    return templates.TemplateResponse(
        request, "finance/ipsas/appropriation_detail.html", context
    )


# =============================================================================
# Commitments
# =============================================================================


@router.get("/commitments", response_class=HTMLResponse)
def list_commitments(
    request: Request,
    fund_id: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Commitment register page."""
    context = base_context(request, auth, "Commitment Register", "ipsas", db=db)
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
        request, "finance/ipsas/commitment_list.html", context
    )


@router.get("/commitments/{commitment_id}", response_class=HTMLResponse)
def view_commitment(
    request: Request,
    commitment_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Commitment detail page."""
    context = base_context(request, auth, "Commitment Detail", "ipsas", db=db)
    web_svc = IPSASWebService(db)
    context.update(web_svc.commitment_detail_context(UUID(commitment_id)))
    return templates.TemplateResponse(
        request, "finance/ipsas/commitment_detail.html", context
    )


# =============================================================================
# Virements
# =============================================================================


@router.get("/virements", response_class=HTMLResponse)
def list_virements(
    request: Request,
    fiscal_year_id: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Virement list page."""
    context = base_context(request, auth, "Virements", "ipsas", db=db)
    web_svc = IPSASWebService(db)
    context.update(
        web_svc.virement_list_context(
            auth.organization_id,
            fiscal_year_id=UUID(fiscal_year_id) if fiscal_year_id else None,
            status=status,
            page=page,
        )
    )
    return templates.TemplateResponse(
        request, "finance/ipsas/virement_list.html", context
    )


@router.get("/virements/new", response_class=HTMLResponse)
def new_virement_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Create virement form page."""
    from app.models.finance.ipsas.appropriation import Appropriation
    from sqlalchemy import select

    context = base_context(request, auth, "New Virement", "ipsas", db=db)
    appropriations = list(
        db.scalars(
            select(Appropriation)
            .where(Appropriation.organization_id == auth.organization_id)
            .order_by(Appropriation.appropriation_code)
        ).all()
    )
    context["appropriations"] = appropriations
    return templates.TemplateResponse(
        request, "finance/ipsas/virement_form.html", context
    )


# =============================================================================
# Reports
# =============================================================================


@router.get("/budget-comparison", response_class=HTMLResponse)
def budget_comparison(
    request: Request,
    fiscal_year_id: Optional[str] = None,
    fund_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """IPSAS 24 Budget vs Actual statement page."""
    context = base_context(request, auth, "Budget Comparison", "ipsas", db=db)
    if fiscal_year_id:
        web_svc = IPSASWebService(db)
        context.update(
            web_svc.budget_comparison_context(
                auth.organization_id,
                UUID(fiscal_year_id),
                fund_id=UUID(fund_id) if fund_id else None,
            )
        )

    # Load fiscal years for selector
    from app.models.finance.gl.fiscal_year import FiscalYear
    from sqlalchemy import select

    fiscal_years = list(
        db.scalars(
            select(FiscalYear)
            .where(FiscalYear.organization_id == auth.organization_id)
            .order_by(FiscalYear.start_date.desc())
        ).all()
    )
    context["fiscal_years"] = fiscal_years
    context["selected_fiscal_year_id"] = fiscal_year_id

    return templates.TemplateResponse(
        request, "finance/ipsas/budget_comparison.html", context
    )


@router.get("/available-balance", response_class=HTMLResponse)
def available_balance_dashboard(
    request: Request,
    fund_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Available balance dashboard page."""
    context = base_context(request, auth, "Available Balance", "ipsas", db=db)
    web_svc = IPSASWebService(db)
    context.update(
        web_svc.available_balance_dashboard_context(
            auth.organization_id,
            fund_id=UUID(fund_id) if fund_id else None,
        )
    )
    return templates.TemplateResponse(
        request, "finance/ipsas/available_balance.html", context
    )
