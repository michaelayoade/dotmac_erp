"""
Tax Web Routes.

HTML template routes for tax periods, returns, and reporting.
"""

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.web.deps import get_db, require_web_auth, WebAuthContext, base_context
from app.services.ifrs.tax import (
    tax_jurisdiction_service,
    tax_code_service,
    tax_period_service,
    tax_return_service,
    deferred_tax_service,
)

templates = Jinja2Templates(directory="templates")

router = APIRouter(prefix="/tax", tags=["tax-web"])


# =============================================================================
# Tax Jurisdictions
# =============================================================================

@router.get("/jurisdictions", response_class=HTMLResponse)
def list_jurisdictions(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    country_code: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Tax jurisdictions list page."""
    limit = 50
    offset = (page - 1) * limit

    jurisdictions = tax_jurisdiction_service.list(
        db=db,
        organization_id=str(auth.organization_id),
        country_code=country_code,
        limit=limit,
        offset=offset,
    )

    context = base_context(request, auth, "Tax Jurisdictions", "tax")
    context.update({
        "jurisdictions": jurisdictions,
        "country_code": country_code,
        "page": page,
    })

    return templates.TemplateResponse(request, "ifrs/tax/jurisdictions.html", context)


# =============================================================================
# Tax Codes
# =============================================================================

@router.get("/codes", response_class=HTMLResponse)
def list_tax_codes(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    tax_type: Optional[str] = None,
    jurisdiction_id: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Tax codes list page."""
    limit = 50
    offset = (page - 1) * limit

    codes = tax_code_service.list(
        db=db,
        organization_id=str(auth.organization_id),
        tax_type=tax_type,
        jurisdiction_id=jurisdiction_id,
        limit=limit,
        offset=offset,
    )

    context = base_context(request, auth, "Tax Codes", "tax")
    context.update({
        "codes": codes,
        "tax_type": tax_type,
        "jurisdiction_id": jurisdiction_id,
        "page": page,
    })

    return templates.TemplateResponse(request, "ifrs/tax/codes.html", context)


# =============================================================================
# Tax Periods
# =============================================================================

@router.get("/periods", response_class=HTMLResponse)
def list_tax_periods(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    jurisdiction_id: Optional[str] = None,
    tax_type: Optional[str] = None,
    status: Optional[str] = None,
    year: Optional[int] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Tax periods list page."""
    limit = 50
    offset = (page - 1) * limit

    periods = tax_period_service.list(
        db=db,
        organization_id=str(auth.organization_id),
        jurisdiction_id=jurisdiction_id,
        tax_type=tax_type,
        status=status,
        year=year,
        limit=limit,
        offset=offset,
    )

    context = base_context(request, auth, "Tax Periods", "tax")
    context.update({
        "periods": periods,
        "jurisdiction_id": jurisdiction_id,
        "tax_type": tax_type,
        "status": status,
        "year": year,
        "page": page,
    })

    return templates.TemplateResponse(request, "ifrs/tax/periods.html", context)


@router.get("/periods/overdue", response_class=HTMLResponse)
def overdue_periods(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    as_of_date: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Overdue tax periods page."""
    check_date = date.fromisoformat(as_of_date) if as_of_date else None
    overdue = tax_period_service.get_overdue_periods(db, auth.organization_id, check_date)

    context = base_context(request, auth, "Overdue Tax Periods", "tax")
    context["overdue_periods"] = overdue
    context["as_of_date"] = as_of_date

    return templates.TemplateResponse(request, "ifrs/tax/overdue_periods.html", context)


# =============================================================================
# Tax Returns
# =============================================================================

@router.get("/returns", response_class=HTMLResponse)
def list_tax_returns(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    period_id: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Tax returns list page."""
    limit = 50
    offset = (page - 1) * limit

    returns = tax_return_service.list(
        db=db,
        organization_id=str(auth.organization_id),
        period_id=period_id,
        status=status,
        limit=limit,
        offset=offset,
    )

    context = base_context(request, auth, "Tax Returns", "tax")
    context.update({
        "returns": returns,
        "period_id": period_id,
        "status": status,
        "page": page,
    })

    return templates.TemplateResponse(request, "ifrs/tax/returns.html", context)


@router.get("/returns/{return_id}", response_class=HTMLResponse)
def view_tax_return(
    request: Request,
    return_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Tax return detail page."""
    tax_return = tax_return_service.get(db, return_id)

    context = base_context(request, auth, "Tax Return Details", "tax")
    context["tax_return"] = tax_return

    return templates.TemplateResponse(request, "ifrs/tax/return_detail.html", context)


@router.get("/returns/new", response_class=HTMLResponse)
def new_return_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New tax return form page."""
    periods = tax_period_service.list(
        db=db,
        organization_id=str(auth.organization_id),
        status="OPEN",
        limit=100,
    )

    context = base_context(request, auth, "Prepare Tax Return", "tax")
    context["periods"] = periods

    return templates.TemplateResponse(request, "ifrs/tax/return_form.html", context)


# =============================================================================
# Deferred Tax
# =============================================================================

@router.get("/deferred", response_class=HTMLResponse)
def deferred_tax_summary(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    as_of_date: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Deferred tax summary page."""
    check_date = as_of_date or date.today().isoformat()

    summary = deferred_tax_service.get_summary(
        db=db,
        organization_id=str(auth.organization_id),
        as_of_date=date.fromisoformat(check_date),
    )

    context = base_context(request, auth, "Deferred Tax Summary", "tax")
    context["summary"] = summary
    context["as_of_date"] = check_date

    return templates.TemplateResponse(request, "ifrs/tax/deferred.html", context)
