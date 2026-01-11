"""
Tax Web Routes.

HTML template routes for tax periods, returns, and reporting.
"""

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.web.deps import get_db, require_web_auth, WebAuthContext, base_context
from app.models.ifrs.tax.tax_period import TaxPeriodFrequency, TaxPeriodStatus
from app.services.ifrs.tax import (
    tax_jurisdiction_service,
    tax_code_service,
    tax_period_service,
    tax_return_service,
    deferred_tax_service,
)
from app.services.ifrs.tax.web import tax_web_service

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
    frequency: Optional[str] = None,
    status: Optional[str] = None,
    year: Optional[int] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Tax periods list page."""
    limit = 50
    offset = (page - 1) * limit

    status_value = None
    if status:
        try:
            status_value = TaxPeriodStatus(status)
        except ValueError:
            status_value = None

    frequency_value = None
    if frequency:
        try:
            frequency_value = TaxPeriodFrequency(frequency)
        except ValueError:
            frequency_value = None

    periods = tax_period_service.list(
        db=db,
        organization_id=str(auth.organization_id),
        jurisdiction_id=jurisdiction_id,
        status=status_value,
        frequency=frequency_value,
        year=year,
        limit=limit,
        offset=offset,
    )

    context = base_context(request, auth, "Tax Periods", "tax")
    context.update({
        "periods": periods,
        "jurisdiction_id": jurisdiction_id,
        "frequency": frequency,
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
        tax_period_id=period_id,
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
    context = base_context(request, auth, "Tax Return Details", "tax")
    context.update(
        tax_web_service.return_detail_context(
            db,
            str(auth.organization_id),
            return_id,
        )
    )

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
        status=TaxPeriodStatus.OPEN,
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


# =============================================================================
# VAT Register & Tax Liability Reports
# =============================================================================

@router.get("/vat-register", response_class=HTMLResponse)
def vat_register(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    transaction_type: Optional[str] = None,
    tax_code_id: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """VAT register page - list of all tax transactions."""
    # Default to current month if no dates provided
    today = date.today()
    if not start_date:
        start = today.replace(day=1)
    else:
        start = date.fromisoformat(start_date)

    if not end_date:
        # Last day of current month
        next_month = today.replace(day=28) + timedelta(days=4)
        end = next_month.replace(day=1) - timedelta(days=1)
    else:
        end = date.fromisoformat(end_date)

    context = base_context(request, auth, "VAT Register", "tax")
    context.update(
        tax_web_service.vat_register_context(
            db=db,
            organization_id=str(auth.organization_id),
            start_date=start,
            end_date=end,
            transaction_type=transaction_type,
            tax_code_id=tax_code_id,
            page=page,
            limit=50,
        )
    )

    return templates.TemplateResponse(request, "ifrs/tax/vat_register.html", context)


@router.get("/liability-summary", response_class=HTMLResponse)
def tax_liability_summary(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    group_by: str = Query(default="month"),
    db: Session = Depends(get_db),
):
    """Tax liability summary page - output vs input tax summary."""
    # Default to last 12 months if no dates provided
    today = date.today()
    if not start_date:
        start = (today.replace(day=1) - timedelta(days=365)).replace(day=1)
    else:
        start = date.fromisoformat(start_date)

    if not end_date:
        end = today
    else:
        end = date.fromisoformat(end_date)

    context = base_context(request, auth, "Tax Liability Summary", "tax")
    context.update(
        tax_web_service.tax_liability_context(
            db=db,
            organization_id=str(auth.organization_id),
            start_date=start,
            end_date=end,
            group_by=group_by,
        )
    )

    return templates.TemplateResponse(request, "ifrs/tax/liability_summary.html", context)


@router.get("/transactions/{transaction_id}", response_class=HTMLResponse)
def view_tax_transaction(
    request: Request,
    transaction_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Tax transaction detail page."""
    context = base_context(request, auth, "Tax Transaction Details", "tax")
    context.update(
        tax_web_service.transaction_detail_context(
            db=db,
            organization_id=str(auth.organization_id),
            transaction_id=transaction_id,
        )
    )

    return templates.TemplateResponse(request, "ifrs/tax/transaction_detail.html", context)


# =============================================================================
# Tax Return Actions
# =============================================================================

@router.get("/returns/{return_id}/transactions", response_class=HTMLResponse)
def return_transactions(
    request: Request,
    return_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """View transactions included in a tax return."""
    context = base_context(request, auth, "Return Transactions", "tax")
    context.update(
        tax_web_service.return_transactions_context(
            db=db,
            organization_id=str(auth.organization_id),
            return_id=return_id,
            page=page,
        )
    )

    return templates.TemplateResponse(request, "ifrs/tax/return_transactions.html", context)


@router.post("/returns/{return_id}/recalculate", response_class=HTMLResponse)
def recalculate_return(
    request: Request,
    return_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Recalculate a draft tax return."""
    try:
        tax_return_service.recalculate(
            db=db,
            organization_id=auth.organization_id,
            return_id=return_id,
        )
    except Exception:
        pass

    return RedirectResponse(
        url=f"/tax/returns/{return_id}",
        status_code=303,
    )


@router.post("/returns/{return_id}/review", response_class=HTMLResponse)
def review_return(
    request: Request,
    return_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Mark a tax return as reviewed."""
    try:
        tax_return_service.review_return(
            db=db,
            organization_id=auth.organization_id,
            return_id=return_id,
            reviewed_by_user_id=auth.person_id,
        )
    except Exception:
        pass

    return RedirectResponse(
        url=f"/tax/returns/{return_id}",
        status_code=303,
    )


@router.post("/returns/{return_id}/file", response_class=HTMLResponse)
def file_return(
    request: Request,
    return_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """File a tax return."""
    try:
        tax_return_service.file_return(
            db=db,
            organization_id=auth.organization_id,
            return_id=return_id,
            filed_by_user_id=auth.person_id,
        )
    except Exception:
        pass

    return RedirectResponse(
        url=f"/tax/returns/{return_id}",
        status_code=303,
    )
