"""
Tax Web Routes.

HTML template routes for tax periods, returns, and reporting.
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.finance.tax.web import tax_web_service
from app.web.deps import WebAuthContext, get_db, require_finance_access

router = APIRouter(prefix="/tax", tags=["tax-web"])


@router.get("/jurisdictions", response_class=HTMLResponse)
def list_jurisdictions(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    country_code: str | None = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Tax jurisdictions list page."""
    return tax_web_service.list_jurisdictions_response(
        request, auth, country_code, page, db
    )


@router.get("/codes", response_class=HTMLResponse)
def list_tax_codes(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    tax_type: str | None = None,
    jurisdiction_id: str | None = None,
    is_active: str | None = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Tax codes list page."""
    # Convert is_active string to boolean
    active_filter = None
    if is_active == "true":
        active_filter = True
    elif is_active == "false":
        active_filter = False

    return tax_web_service.list_tax_codes_response(
        request,
        auth,
        tax_type,
        jurisdiction_id,
        page,
        db,
        is_active=active_filter,
    )


@router.get("/codes/new", response_class=HTMLResponse)
def new_tax_code_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New tax code form page."""
    return tax_web_service.new_tax_code_form_response(request, auth, db)


@router.post("/codes/new", response_class=HTMLResponse)
async def create_tax_code(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Create a new tax code."""
    return await tax_web_service.create_tax_code_response(request, auth, db)


@router.get("/codes/{tax_code_id}/edit", response_class=HTMLResponse)
def edit_tax_code_form(
    request: Request,
    tax_code_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Edit tax code form page."""
    return tax_web_service.edit_tax_code_form_response(request, auth, tax_code_id, db)


@router.post("/codes/{tax_code_id}/edit", response_class=HTMLResponse)
async def update_tax_code(
    request: Request,
    tax_code_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Update an existing tax code."""
    return await tax_web_service.update_tax_code_response(
        request, auth, tax_code_id, db
    )


@router.post("/codes/{tax_code_id}/toggle", response_class=HTMLResponse)
def toggle_tax_code(
    tax_code_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Toggle tax code active/inactive status."""
    return tax_web_service.toggle_tax_code_response(auth, tax_code_id, db)


@router.get("/periods", response_class=HTMLResponse)
def list_tax_periods(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    jurisdiction_id: str | None = None,
    frequency: str | None = None,
    status: str | None = None,
    year: int | None = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Tax periods list page."""
    return tax_web_service.list_tax_periods_response(
        request,
        auth,
        jurisdiction_id,
        frequency,
        status,
        year,
        page,
        db,
    )


@router.get("/periods/overdue", response_class=HTMLResponse)
def overdue_periods(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    as_of_date: str | None = None,
    db: Session = Depends(get_db),
):
    """Overdue tax periods page."""
    return tax_web_service.overdue_periods_response(request, auth, as_of_date, db)


@router.get("/returns", response_class=HTMLResponse)
def list_tax_returns(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    period_id: str | None = None,
    return_type: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Tax returns list page."""
    return tax_web_service.list_tax_returns_response(
        request, auth, period_id, return_type, status, page, db
    )


@router.get("/returns/new", response_class=HTMLResponse)
def new_return_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New tax return form page."""
    return tax_web_service.new_return_form_response(request, auth, db)


@router.post("/returns/new", response_class=HTMLResponse)
async def create_return(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Create a new tax return."""
    return await tax_web_service.create_return_response(request, auth, db)


@router.get("/returns/{return_id}", response_class=HTMLResponse)
def view_tax_return(
    request: Request,
    return_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Tax return detail page."""
    return tax_web_service.view_tax_return_response(request, auth, return_id, db)


@router.get("/returns/{return_id}/edit", response_class=HTMLResponse)
def edit_return_form(
    request: Request,
    return_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Edit tax return form page."""
    return tax_web_service.edit_return_form_response(request, auth, return_id, db)


@router.post("/returns/{return_id}/edit", response_class=HTMLResponse)
async def update_return(
    request: Request,
    return_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Update a tax return."""
    return await tax_web_service.update_return_response(request, auth, return_id, db)


@router.get("/deferred", response_class=HTMLResponse)
def deferred_tax_summary(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    as_of_date: str | None = None,
    db: Session = Depends(get_db),
):
    """Deferred tax summary page."""
    return tax_web_service.deferred_tax_summary_response(request, auth, as_of_date, db)


@router.get("/vat-register", response_class=HTMLResponse)
def vat_register(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    start_date: str | None = None,
    end_date: str | None = None,
    transaction_type: str | None = None,
    tax_code_id: str | None = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """VAT register page - list of all tax transactions."""
    return tax_web_service.vat_register_response(
        request,
        auth,
        start_date,
        end_date,
        transaction_type,
        tax_code_id,
        page,
        db,
    )


@router.get("/liability-summary", response_class=HTMLResponse)
def tax_liability_summary(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    start_date: str | None = None,
    end_date: str | None = None,
    group_by: str = Query(default="month"),
    db: Session = Depends(get_db),
):
    """Tax liability summary page - output vs input tax summary."""
    return tax_web_service.tax_liability_summary_response(
        request,
        auth,
        start_date,
        end_date,
        group_by,
        db,
    )


@router.get("/transactions/{transaction_id}", response_class=HTMLResponse)
def view_tax_transaction(
    request: Request,
    transaction_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Tax transaction detail page."""
    return tax_web_service.view_tax_transaction_response(
        request, auth, transaction_id, db
    )


@router.get("/returns/{return_id}/transactions", response_class=HTMLResponse)
def return_transactions(
    request: Request,
    return_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """View transactions included in a tax return."""
    return tax_web_service.return_transactions_response(
        request, auth, return_id, page, db
    )


@router.post("/returns/{return_id}/recalculate", response_class=HTMLResponse)
def recalculate_return(
    return_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Recalculate a draft tax return."""
    return tax_web_service.recalculate_return_response(return_id, auth, db)


@router.post("/returns/{return_id}/review", response_class=HTMLResponse)
def review_return(
    return_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Mark a tax return as reviewed."""
    return tax_web_service.review_return_response(return_id, auth, db)


@router.post("/returns/{return_id}/file", response_class=HTMLResponse)
def file_return(
    return_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """File a tax return."""
    return tax_web_service.file_return_response(return_id, auth, db)


# ============================================================
# Tax Reports
# ============================================================


@router.get("/reports/by-type", response_class=HTMLResponse)
def tax_summary_by_type(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Tax summary report grouped by tax type."""
    return tax_web_service.tax_summary_by_type_page(
        request, start_date, end_date, auth, db
    )


@router.get("/reports/wht", response_class=HTMLResponse)
def wht_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    include_details: bool = True,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Withholding tax report."""
    return tax_web_service.wht_report_page(
        request, start_date, end_date, include_details, auth, db
    )
