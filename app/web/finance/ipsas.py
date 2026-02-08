"""
IPSAS Web Routes.

HTML template routes for IPSAS Fund Accounting, Appropriations,
Commitments, Virements, and Budget Comparison.
"""

from decimal import Decimal
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
    status: str | None = None,
    fund_type: str | None = None,
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
    context.update(web_svc.fund_detail_context(auth.organization_id, UUID(fund_id)))
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
    is_restricted: str | None = Form(None),
    donor_name: str | None = Form(None),
    donor_reference: str | None = Form(None),
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
    fiscal_year_id: str | None = None,
    fund_id: str | None = None,
    status: str | None = None,
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
    from sqlalchemy import select

    from app.models.finance.gl.fiscal_year import FiscalYear
    from app.models.finance.ipsas.fund import Fund

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


@router.post("/appropriations/new")
def create_appropriation(
    request: Request,
    fiscal_year_id: str = Form(...),
    fund_id: str = Form(...),
    appropriation_code: str = Form(...),
    appropriation_name: str = Form(...),
    appropriation_type: str = Form(...),
    approved_amount: str = Form(...),
    currency_code: str = Form("NGN"),
    effective_from: str = Form(...),
    budget_id: str | None = Form(None),
    account_id: str | None = Form(None),
    cost_center_id: str | None = Form(None),
    business_unit_id: str | None = Form(None),
    appropriation_act_reference: str | None = Form(None),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
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
        currency_code=currency_code,
        effective_from=date_type.fromisoformat(effective_from),
        budget_id=UUID(budget_id) if budget_id else None,
        account_id=UUID(account_id) if account_id else None,
        cost_center_id=UUID(cost_center_id) if cost_center_id else None,
        business_unit_id=UUID(business_unit_id) if business_unit_id else None,
        appropriation_act_reference=appropriation_act_reference or None,
    )
    svc = AppropriationService(db)
    approp = svc.create(auth.organization_id, data, auth.user_id)
    db.commit()
    return RedirectResponse(
        f"/finance/ipsas/appropriations/{approp.appropriation_id}", status_code=303
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
    context.update(
        web_svc.appropriation_detail_context(
            auth.organization_id, UUID(appropriation_id)
        )
    )
    return templates.TemplateResponse(
        request, "finance/ipsas/appropriation_detail.html", context
    )


@router.post("/appropriations/{appropriation_id}/approve")
def approve_appropriation(
    request: Request,
    appropriation_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Approve an appropriation (web form submission)."""
    from app.services.finance.ipsas.appropriation_service import AppropriationService

    svc = AppropriationService(db)
    svc.get_or_404(UUID(appropriation_id), auth.organization_id)
    svc.approve(UUID(appropriation_id), auth.user_id)
    db.commit()
    return RedirectResponse(
        f"/finance/ipsas/appropriations/{appropriation_id}", status_code=303
    )


# =============================================================================
# Commitments
# =============================================================================


@router.get("/commitments", response_class=HTMLResponse)
def list_commitments(
    request: Request,
    fund_id: str | None = None,
    status: str | None = None,
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


@router.get("/commitments/new", response_class=HTMLResponse)
def new_commitment_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Create commitment form page."""
    from sqlalchemy import select

    from app.models.finance.gl.account import Account
    from app.models.finance.gl.fiscal_period import FiscalPeriod
    from app.models.finance.gl.fiscal_year import FiscalYear
    from app.models.finance.ipsas.appropriation import Appropriation
    from app.models.finance.ipsas.fund import Fund

    context = base_context(request, auth, "New Commitment", "ipsas", db=db)
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
        request, "finance/ipsas/commitment_form.html", context
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
    currency_code: str = Form("NGN"),
    appropriation_id: str | None = Form(None),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
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
        currency_code=currency_code,
        created_by_user_id=auth.user_id,
        appropriation_id=UUID(appropriation_id) if appropriation_id else None,
    )
    db.commit()
    return RedirectResponse(
        f"/finance/ipsas/commitments/{commitment.commitment_id}", status_code=303
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
    context.update(
        web_svc.commitment_detail_context(auth.organization_id, UUID(commitment_id))
    )
    return templates.TemplateResponse(
        request, "finance/ipsas/commitment_detail.html", context
    )


# =============================================================================
# Virements
# =============================================================================


@router.get("/virements", response_class=HTMLResponse)
def list_virements(
    request: Request,
    fiscal_year_id: str | None = None,
    status: str | None = None,
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
    from sqlalchemy import select

    from app.models.finance.ipsas.appropriation import Appropriation

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


@router.post("/virements/new")
def create_virement(
    request: Request,
    description: str = Form(...),
    from_appropriation_id: str = Form(...),
    to_appropriation_id: str = Form(...),
    amount: str = Form(...),
    currency_code: str = Form("NGN"),
    justification: str = Form(...),
    approval_authority: str | None = Form(None),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Create a virement (form submission)."""
    # Resolve fiscal_year_id from the source appropriation
    from app.models.finance.ipsas.appropriation import Appropriation
    from app.schemas.finance.ipsas import VirementCreate
    from app.services.finance.ipsas.virement_service import VirementService

    from_approp = db.get(Appropriation, UUID(from_appropriation_id))
    fiscal_year_id = from_approp.fiscal_year_id if from_approp else None
    if not fiscal_year_id:
        return RedirectResponse(
            "/finance/ipsas/virements?error=invalid_appropriation", status_code=303
        )

    data = VirementCreate(
        fiscal_year_id=fiscal_year_id,
        description=description,
        from_appropriation_id=UUID(from_appropriation_id),
        to_appropriation_id=UUID(to_appropriation_id),
        amount=Decimal(amount),
        currency_code=currency_code,
        justification=justification,
        approval_authority=approval_authority or None,
    )

    svc = VirementService(db)
    org_id = auth.organization_id
    assert org_id is not None
    virement_number = (
        f"VIR-{org_id.hex[:6].upper()}-{svc.count_for_org(org_id) + 1:04d}"
    )
    svc.create(auth.organization_id, data, auth.user_id, virement_number)
    db.commit()
    return RedirectResponse("/finance/ipsas/virements", status_code=303)


@router.get("/virements/{virement_id}", response_class=HTMLResponse)
def view_virement(
    request: Request,
    virement_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Virement detail page."""
    context = base_context(request, auth, "Virement Detail", "ipsas", db=db)
    web_svc = IPSASWebService(db)
    context.update(
        web_svc.virement_detail_context(auth.organization_id, UUID(virement_id))
    )
    return templates.TemplateResponse(
        request, "finance/ipsas/virement_detail.html", context
    )


@router.post("/virements/{virement_id}/approve")
def approve_virement(
    request: Request,
    virement_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Approve a virement (form submission)."""
    from app.services.finance.ipsas.virement_service import VirementService

    svc = VirementService(db)
    svc.get_or_404(UUID(virement_id), organization_id=auth.organization_id)
    svc.approve(UUID(virement_id), auth.user_id)
    db.commit()
    return RedirectResponse(f"/finance/ipsas/virements/{virement_id}", status_code=303)


@router.post("/virements/{virement_id}/apply")
def apply_virement(
    request: Request,
    virement_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Apply an approved virement (form submission)."""
    from app.services.finance.ipsas.virement_service import VirementService

    svc = VirementService(db)
    svc.get_or_404(UUID(virement_id), organization_id=auth.organization_id)
    svc.apply(UUID(virement_id))
    db.commit()
    return RedirectResponse(f"/finance/ipsas/virements/{virement_id}", status_code=303)


# =============================================================================
# Reports
# =============================================================================


@router.get("/budget-comparison", response_class=HTMLResponse)
def budget_comparison(
    request: Request,
    fiscal_year_id: str | None = None,
    fund_id: str | None = None,
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
    from sqlalchemy import select

    from app.models.finance.gl.fiscal_year import FiscalYear

    fiscal_years = list(
        db.scalars(
            select(FiscalYear)
            .where(FiscalYear.organization_id == auth.organization_id)
            .order_by(FiscalYear.start_date.desc())
        ).all()
    )
    context["fiscal_years"] = fiscal_years
    context["selected_fiscal_year_id"] = fiscal_year_id
    context["selected_fund_id"] = fund_id

    # Load funds for filter
    from app.models.finance.ipsas.fund import Fund

    funds = list(
        db.scalars(
            select(Fund)
            .where(Fund.organization_id == auth.organization_id)
            .order_by(Fund.fund_code)
        ).all()
    )
    context["funds"] = funds

    return templates.TemplateResponse(
        request, "finance/ipsas/budget_comparison.html", context
    )


@router.get("/available-balance", response_class=HTMLResponse)
def available_balance_dashboard(
    request: Request,
    fund_id: str | None = None,
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
