"""
Tax Web Routes.

HTML template routes for tax periods, returns, and reporting.
"""

import io
from datetime import date

from fastapi import APIRouter, Depends, Query, Request

from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse

from app.services.finance.tax.control_tracker import tax_control_tracker_service
from app.services.finance.tax.web import tax_web_service
from app.templates import templates
from app.web.deps import (
    get_db_for_org,
    WebAuthContext,
    base_context,
    require_finance_access,
    require_web_permission,
)

router = APIRouter(prefix="/tax", tags=["tax-web"])


def _csv_response(content: str, filename: str) -> StreamingResponse:
    """Build a StreamingResponse for CSV download."""
    return StreamingResponse(
        io.BytesIO(content.encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def tax_landing(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
):
    """Tax landing page."""
    context = base_context(request, auth, "Tax", "tax")
    return templates.TemplateResponse(request, "finance/tax/index.html", context)


@router.get("/jurisdictions", response_class=HTMLResponse)
def list_jurisdictions(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    country_code: str | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=10, le=500),
    db: Session = Depends(get_db_for_org),
):
    """Tax jurisdictions list page."""
    return tax_web_service.list_jurisdictions_response(
        request=request,
        auth=auth,
        country_code=country_code,
        page=page,
        limit=limit,
        db=db,
    )


@router.get("/codes", response_class=HTMLResponse)
def list_tax_codes(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    tax_type: str | None = None,
    jurisdiction_id: str | None = None,
    is_active: str | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=10, le=500),
    db: Session = Depends(get_db_for_org),
):
    """Tax codes list page."""
    # Convert is_active string to boolean
    active_filter = None
    if is_active == "true":
        active_filter = True
    elif is_active == "false":
        active_filter = False

    return tax_web_service.list_tax_codes_response(
        request=request,
        auth=auth,
        tax_type=tax_type,
        jurisdiction_id=jurisdiction_id,
        page=page,
        limit=limit,
        db=db,
        is_active=active_filter,
    )


@router.get("/codes/new", response_class=HTMLResponse)
def new_tax_code_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """New tax code form page."""
    return tax_web_service.new_tax_code_form_response(request, auth, db)


@router.post("/codes/new", response_class=HTMLResponse)
async def create_tax_code(
    request: Request,
    auth: WebAuthContext = Depends(require_web_permission("tax:codes:create")),
    db: Session = Depends(get_db_for_org),
):
    """Create a new tax code."""
    return await tax_web_service.create_tax_code_response(request, auth, db)


@router.get("/codes/{tax_code_id}/edit", response_class=HTMLResponse)
def edit_tax_code_form(
    request: Request,
    tax_code_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit tax code form page."""
    return tax_web_service.edit_tax_code_form_response(request, auth, tax_code_id, db)


@router.post("/codes/{tax_code_id}/edit", response_class=HTMLResponse)
async def update_tax_code(
    request: Request,
    tax_code_id: str,
    auth: WebAuthContext = Depends(require_web_permission("tax:codes:manage")),
    db: Session = Depends(get_db_for_org),
):
    """Update an existing tax code."""
    return await tax_web_service.update_tax_code_response(
        request, auth, tax_code_id, db
    )


@router.post("/codes/{tax_code_id}/toggle", response_class=HTMLResponse)
def toggle_tax_code(
    tax_code_id: str,
    auth: WebAuthContext = Depends(require_web_permission("tax:codes:manage")),
    db: Session = Depends(get_db_for_org),
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
    limit: int = Query(default=50, ge=10, le=500),
    db: Session = Depends(get_db_for_org),
):
    """Tax periods list page."""
    return tax_web_service.list_tax_periods_response(
        request=request,
        auth=auth,
        jurisdiction_id=jurisdiction_id,
        frequency=frequency,
        status=status,
        year=year,
        page=page,
        limit=limit,
        db=db,
    )


@router.get("/periods/overdue", response_class=HTMLResponse)
def overdue_periods(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    as_of_date: str | None = None,
    db: Session = Depends(get_db_for_org),
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
    limit: int = Query(default=50, ge=10, le=500),
    db: Session = Depends(get_db_for_org),
):
    """Tax returns list page."""
    return tax_web_service.list_tax_returns_response(
        request=request,
        auth=auth,
        period_id=period_id,
        return_type=return_type,
        status=status,
        page=page,
        limit=limit,
        db=db,
    )


@router.get("/returns/new", response_class=HTMLResponse)
def new_return_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """New tax return form page."""
    return tax_web_service.new_return_form_response(request, auth, db)


@router.post("/returns/new", response_class=HTMLResponse)
async def create_return(
    request: Request,
    auth: WebAuthContext = Depends(require_web_permission("tax:returns:create")),
    db: Session = Depends(get_db_for_org),
):
    """Create a new tax return."""
    return await tax_web_service.create_return_response(request, auth, db)


@router.get("/returns/{return_id}", response_class=HTMLResponse)
def view_tax_return(
    request: Request,
    return_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Tax return detail page."""
    return tax_web_service.view_tax_return_response(request, auth, return_id, db)


@router.get("/returns/{return_id}/edit", response_class=HTMLResponse)
def edit_return_form(
    request: Request,
    return_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit tax return form page."""
    return tax_web_service.edit_return_form_response(request, auth, return_id, db)


@router.post("/returns/{return_id}/edit", response_class=HTMLResponse)
async def update_return(
    request: Request,
    return_id: str,
    auth: WebAuthContext = Depends(require_web_permission("tax:returns:create")),
    db: Session = Depends(get_db_for_org),
):
    """Update a tax return."""
    return await tax_web_service.update_return_response(request, auth, return_id, db)


@router.get("/deferred", response_class=HTMLResponse)
def deferred_tax_summary(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    as_of_date: str | None = None,
    db: Session = Depends(get_db_for_org),
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
    db: Session = Depends(get_db_for_org),
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
    db: Session = Depends(get_db_for_org),
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


@router.get("/control-tracker", response_class=HTMLResponse)
def tax_control_tracker(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    year: int | None = Query(default=None, ge=2000, le=2100),
    db: Session = Depends(get_db_for_org),
):
    """VAT/WHT control tracker built from source-specific bases."""
    return tax_web_service.tax_control_tracker_response(
        request=request,
        auth=auth,
        year=year,
        db=db,
    )


@router.get("/control-tracker/customer-deductions/export")
def export_tax_control_customer_deductions(
    year: int | None = Query(default=None, ge=2000, le=2100),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> StreamingResponse:
    """Export tracker customer deduction rows as CSV."""
    tracker_year = year or (date.today().year - 1)
    csv = tax_control_tracker_service.export_customer_deductions_csv(
        db=db,
        organization_id=str(auth.organization_id),
        year=tracker_year,
    )
    return _csv_response(csv, f"tax_control_customer_deductions_{tracker_year}.csv")


@router.get("/control-tracker/supplier-wht/export")
def export_tax_control_supplier_wht(
    year: int | None = Query(default=None, ge=2000, le=2100),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> StreamingResponse:
    """Export tracker supplier WHT rows as CSV."""
    tracker_year = year or (date.today().year - 1)
    csv = tax_control_tracker_service.export_supplier_wht_csv(
        db=db,
        organization_id=str(auth.organization_id),
        year=tracker_year,
    )
    return _csv_response(csv, f"tax_control_supplier_wht_{tracker_year}.csv")


@router.post("/control-tracker/evidence", response_class=HTMLResponse)
async def save_tax_control_evidence(
    request: Request,
    auth: WebAuthContext = Depends(require_web_permission("tax:wht:manage")),
    db: Session = Depends(get_db_for_org),
):
    """Save manual evidence tracking for customer certificates or supplier remittances."""
    form = await request.form()
    year = int(form.get("year") or (date.today().year - 1))
    evidence_type = str(form.get("evidence_type") or "").strip().upper()
    entity_type = str(form.get("entity_type") or "").strip().upper()
    entity_id = str(form.get("entity_id") or "").strip()
    status = str(form.get("status") or "MISSING").strip().upper()
    reference = str(form.get("reference") or "").strip()
    notes = str(form.get("notes") or "").strip()

    tax_control_tracker_service.upsert_evidence(
        db=db,
        organization_id=str(auth.organization_id),
        year=year,
        evidence_type=evidence_type,
        entity_type=entity_type,
        entity_id=entity_id,
        status=status,
        reference=reference,
        notes=notes,
        updated_by_user_id=str(auth.person_id),
    )

    return RedirectResponse(
        url=f"/finance/tax/control-tracker?year={year}&saved=1",
        status_code=303,
    )


@router.get("/transactions/{transaction_id}", response_class=HTMLResponse)
def view_tax_transaction(
    request: Request,
    transaction_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
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
    db: Session = Depends(get_db_for_org),
):
    """View transactions included in a tax return."""
    return tax_web_service.return_transactions_response(
        request, auth, return_id, page, db
    )


@router.post("/returns/{return_id}/recalculate", response_class=HTMLResponse)
def recalculate_return(
    return_id: str,
    auth: WebAuthContext = Depends(require_web_permission("tax:returns:create")),
    db: Session = Depends(get_db_for_org),
):
    """Recalculate a draft tax return."""
    return tax_web_service.recalculate_return_response(return_id, auth, db)


@router.post("/returns/{return_id}/review", response_class=HTMLResponse)
def review_return(
    return_id: str,
    auth: WebAuthContext = Depends(require_web_permission("tax:returns:review")),
    db: Session = Depends(get_db_for_org),
):
    """Mark a tax return as reviewed."""
    return tax_web_service.review_return_response(return_id, auth, db)


@router.post("/returns/{return_id}/file", response_class=HTMLResponse)
def file_return(
    return_id: str,
    auth: WebAuthContext = Depends(require_web_permission("tax:returns:file")),
    db: Session = Depends(get_db_for_org),
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
    basis: str = "accrual",
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Tax summary report grouped by tax type."""
    return tax_web_service.tax_summary_by_type_page(
        request, start_date, end_date, auth, db, basis=basis
    )


@router.get("/reports/wht", response_class=HTMLResponse)
def wht_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    include_details: bool = True,
    basis: str = "accrual",
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Withholding tax report."""
    return tax_web_service.wht_report_page(
        request, start_date, end_date, include_details, auth, db, basis=basis
    )


@router.get("/reports/stamp-duty", response_class=HTMLResponse)
def stamp_duty_report(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    include_details: bool = True,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Stamp duty report."""
    return tax_web_service.stamp_duty_report_page(
        request, start_date, end_date, include_details, auth, db
    )


@router.get("/reports/vat-return", response_class=HTMLResponse)
def vat_return(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    basis: str = "cash",
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """VAT return (FIRS Form 002). Defaults to cash basis (Nigerian filing rule)."""
    return tax_web_service.vat_return_page(
        request, start_date, end_date, auth, db, basis=basis
    )


# ─── Fiscal Positions ───────────────────────────────────────────────


@router.get("/fiscal-positions", response_class=HTMLResponse)
def list_fiscal_positions(
    request: Request,
    search: str | None = None,
    is_active: str | None = None,
    page: int = 1,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Fiscal positions list page."""
    from app.services.finance.tax.fiscal_position_web import (
        fiscal_position_web_service,
    )

    return fiscal_position_web_service.list_response(
        request, auth, db, search=search, is_active=is_active, page=page
    )


@router.get("/fiscal-positions/new", response_class=HTMLResponse)
def new_fiscal_position(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Fiscal position creation form."""
    from app.services.finance.tax.fiscal_position_web import (
        fiscal_position_web_service,
    )

    return fiscal_position_web_service.form_response(request, auth, db)


@router.post("/fiscal-positions/new", response_class=HTMLResponse)
async def create_fiscal_position(
    request: Request,
    auth: WebAuthContext = Depends(require_web_permission("tax:codes:manage")),
    db: Session = Depends(get_db_for_org),
):
    """Handle fiscal position creation."""
    from app.services.finance.tax.fiscal_position_web import (
        fiscal_position_web_service,
    )

    form = await request.form()
    form_data = dict(form)
    response = fiscal_position_web_service.create_response(request, auth, db, form_data)
    if isinstance(response, RedirectResponse):
        db.commit()
    return response


@router.get("/fiscal-positions/{fiscal_position_id}", response_class=HTMLResponse)
def fiscal_position_detail(
    request: Request,
    fiscal_position_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Fiscal position detail page."""
    from app.services.finance.tax.fiscal_position_web import (
        fiscal_position_web_service,
    )

    return fiscal_position_web_service.detail_response(
        request, auth, fiscal_position_id, db
    )


@router.get("/fiscal-positions/{fiscal_position_id}/edit", response_class=HTMLResponse)
def edit_fiscal_position(
    request: Request,
    fiscal_position_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Fiscal position edit form."""
    from app.services.finance.tax.fiscal_position_web import (
        fiscal_position_web_service,
    )

    return fiscal_position_web_service.form_response(
        request, auth, db, fiscal_position_id=fiscal_position_id
    )


@router.post("/fiscal-positions/{fiscal_position_id}/edit", response_class=HTMLResponse)
async def update_fiscal_position(
    request: Request,
    fiscal_position_id: str,
    auth: WebAuthContext = Depends(require_web_permission("tax:codes:manage")),
    db: Session = Depends(get_db_for_org),
):
    """Handle fiscal position update."""
    from app.services.finance.tax.fiscal_position_web import (
        fiscal_position_web_service,
    )

    form = await request.form()
    form_data = dict(form)
    response = fiscal_position_web_service.update_response(
        request, auth, fiscal_position_id, db, form_data
    )
    if isinstance(response, RedirectResponse):
        db.commit()
    return response


@router.post(
    "/fiscal-positions/{fiscal_position_id}/delete", response_class=HTMLResponse
)
def delete_fiscal_position(
    request: Request,
    fiscal_position_id: str,
    auth: WebAuthContext = Depends(require_web_permission("tax:codes:manage")),
    db: Session = Depends(get_db_for_org),
):
    """Handle fiscal position deletion."""
    from app.services.finance.tax.fiscal_position_web import (
        fiscal_position_web_service,
    )

    response = fiscal_position_web_service.delete_response(
        request, auth, fiscal_position_id, db
    )
    db.commit()
    return response
