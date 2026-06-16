"""
GL (General Ledger) Web Routes.

HTML template routes for Chart of Accounts, Journal Entries, and Fiscal Periods.
"""

from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from sqlalchemy.orm import Session

from app.services.common import coerce_uuid
from app.services.finance.gl.web import gl_web_service
from app.services.finance.gl.web.fx_revaluation_web import FXRevaluationWebService
from app.services.finance.rpt.async_exports import (
    get_completed_export_for_download,
    get_export_status,
    queue_background_export,
)
from app.templates import templates
from app.web.deps import (
    WebAuthContext,
    base_context,
    get_db_for_org,
    require_finance_access,
    require_finance_admin,
)

router = APIRouter(prefix="/gl", tags=["gl-web"])


@router.get("/accounts", response_class=HTMLResponse)
def list_accounts(
    request: Request,
    search: str | None = None,
    category: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    sort: str | None = None,
    sort_dir: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Chart of Accounts list page."""
    return gl_web_service.list_accounts_response(
        request,
        auth,
        db,
        search,
        category,
        status,
        page,
        sort,
        sort_dir,
    )


@router.get("/accounts/new", response_class=HTMLResponse)
def new_account_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """New account form page."""
    return gl_web_service.account_new_form_response(request, auth, db)


@router.get("/accounts/suggest-code/{category_id}")
def suggest_account_code(
    category_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """
    Suggest the next account code for a category.

    Returns JSON with suggested_code based on standard chart of accounts numbering.
    """
    from app.services.finance.gl.chart_of_accounts import chart_of_accounts_service

    return chart_of_accounts_service.suggest_next_code(
        db,
        str(auth.organization_id),
        category_id,
    )


@router.get("/accounts/export")
async def export_all_accounts(
    request: Request,
    search: str = "",
    status: str = "",
    category: str = "",
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Export all accounts matching filters to CSV."""
    return await gl_web_service.export_all_accounts_response(
        auth, db, search, status, category=category
    )


@router.get("/accounts/category-tree")
def account_category_tree(
    active_only: bool = True,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Return the category hierarchy as a depth-annotated flat list (JSON)."""
    return gl_web_service.category_tree_response(
        db,
        str(auth.organization_id),
        active_only=active_only,
    )


@router.get("/accounts/{account_id}", response_class=HTMLResponse)
def view_account(
    request: Request,
    account_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Account detail page."""
    return gl_web_service.account_detail_response(
        request,
        auth,
        db,
        account_id,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/accounts/{account_id}/edit", response_class=HTMLResponse)
def edit_account_form(
    request: Request,
    account_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit account form page."""
    return gl_web_service.account_edit_form_response(request, auth, db, account_id)


@router.post("/accounts/new")
def create_account(
    request: Request,
    account_code: str = Form(...),
    account_name: str = Form(...),
    category_id: str = Form(...),
    account_type: str = Form(...),
    normal_balance: str = Form(...),
    description: str = Form(""),
    search_terms: str = Form(""),
    is_multi_currency: str | None = Form(None),
    default_currency_code: str | None = Form(None),
    is_active: str | None = Form(None),
    is_posting_allowed: str | None = Form(None),
    is_budgetable: str | None = Form(None),
    is_reconciliation_required: str | None = Form(None),
    subledger_type: str | None = Form(None),
    is_cash_equivalent: str | None = Form(None),
    is_financial_instrument: str | None = Form(None),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Create a new GL account."""
    # HTML checkboxes send nothing when unchecked, so we check for presence
    return gl_web_service.create_account_response(
        request,
        auth,
        db,
        account_code,
        account_name,
        category_id,
        account_type,
        normal_balance,
        description,
        search_terms,
        is_multi_currency is not None,
        default_currency_code,
        is_active is not None,
        is_posting_allowed is not None,
        is_budgetable is not None,
        is_reconciliation_required is not None,
        subledger_type,
        is_cash_equivalent is not None,
        is_financial_instrument is not None,
    )


@router.post("/accounts/{account_id}/edit")
def update_account(
    request: Request,
    account_id: str,
    account_code: str = Form(...),
    account_name: str = Form(...),
    category_id: str = Form(...),
    account_type: str = Form(...),
    normal_balance: str = Form(...),
    description: str = Form(""),
    search_terms: str = Form(""),
    is_multi_currency: str | None = Form(None),
    default_currency_code: str | None = Form(None),
    is_active: str | None = Form(None),
    is_posting_allowed: str | None = Form(None),
    is_budgetable: str | None = Form(None),
    is_reconciliation_required: str | None = Form(None),
    subledger_type: str | None = Form(None),
    is_cash_equivalent: str | None = Form(None),
    is_financial_instrument: str | None = Form(None),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Update an existing GL account."""
    # HTML checkboxes send nothing when unchecked, so we check for presence
    return gl_web_service.update_account_response(
        request,
        auth,
        db,
        account_id,
        account_code,
        account_name,
        category_id,
        account_type,
        normal_balance,
        description,
        search_terms,
        is_multi_currency is not None,
        default_currency_code,
        is_active is not None,
        is_posting_allowed is not None,
        is_budgetable is not None,
        is_reconciliation_required is not None,
        subledger_type,
        is_cash_equivalent is not None,
        is_financial_instrument is not None,
    )


@router.post("/accounts/{account_id}/delete")
def delete_account(
    request: Request,
    account_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Delete a GL account."""
    return gl_web_service.delete_account_response(request, auth, db, account_id)


# ═══════════════════════════════════════════════════════════════════
# Bulk Actions - Accounts
# ═══════════════════════════════════════════════════════════════════


@router.post("/accounts/bulk-delete")
async def bulk_delete_accounts(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Bulk delete accounts (if no journal entries)."""
    return await gl_web_service.bulk_delete_accounts_response(request, auth, db)


@router.post("/accounts/bulk-export")
async def bulk_export_accounts(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Export selected accounts to CSV."""
    return await gl_web_service.bulk_export_accounts_response(request, auth, db)


@router.post("/accounts/bulk-activate")
async def bulk_activate_accounts(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Bulk activate accounts."""
    return await gl_web_service.bulk_activate_accounts_response(request, auth, db)


@router.post("/accounts/bulk-deactivate")
async def bulk_deactivate_accounts(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Bulk deactivate accounts."""
    return await gl_web_service.bulk_deactivate_accounts_response(request, auth, db)


# ═══════════════════════════════════════════════════════════════════
# Ledger Transactions
# ═══════════════════════════════════════════════════════════════════


@router.get("/ledger", response_class=HTMLResponse)
def list_ledger(
    request: Request,
    account_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """General Ledger transactions page - all posted ledger entries."""
    return gl_web_service.list_ledger_response(
        request,
        auth,
        db,
        account_id,
        start_date,
        end_date,
        search,
        page,
    )


@router.get("/ledger/export")
async def export_all_ledger(
    search: str = "",
    account_id: str = "",
    start_date: str = "",
    end_date: str = "",
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Export all posted ledger transactions matching filters to CSV."""
    return await gl_web_service.export_all_ledger_response(
        auth, db, search, account_id, start_date, end_date
    )


@router.post("/ledger/export")
async def export_or_queue_ledger(
    search: str = "",
    account_id: str = "",
    start_date: str = "",
    end_date: str = "",
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> Response:
    """
    Export ledger transactions, inline for small result sets or queued (with an
    emailed download link) for large ones. Returns CSV (200) or queued JSON (202).
    """
    return await gl_web_service.export_or_queue_ledger_response(
        auth, db, search, account_id, start_date, end_date
    )


@router.get("/ledger/exports/{instance_id}/download")
def download_ledger_export(
    instance_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> Response:
    """Download a completed queued Ledger Transactions export."""
    body, filename, media_type, content_length = get_completed_export_for_download(
        db,
        auth.organization_id,
        auth.user_id,
        instance_id,
        report_code="GL_LEDGER",
    )
    if hasattr(body, "__fspath__"):
        return FileResponse(body, filename=filename, media_type=media_type)

    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    return StreamingResponse(body, media_type=media_type, headers=headers)


@router.get("/ledger/exports/{instance_id}/status")
def ledger_export_status(
    instance_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> JSONResponse:
    """Return the status of a queued Ledger Transactions export."""
    return JSONResponse(
        get_export_status(
            db,
            auth.organization_id,
            auth.user_id,
            instance_id,
            report_code="GL_LEDGER",
        )
    )


@router.get("/journals", response_class=HTMLResponse)
def list_journals(
    request: Request,
    search: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = Query(default=1, ge=1),
    sort: str | None = None,
    sort_dir: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Journal entries list page."""
    return gl_web_service.list_journals_response(
        request,
        auth,
        db,
        search,
        status,
        start_date,
        end_date,
        page,
        sort,
        sort_dir,
    )


@router.get("/journals/new", response_class=HTMLResponse)
def new_journal_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """New journal entry form page."""
    return gl_web_service.journal_new_form_response(request, auth, db)


@router.get("/journals/export")
async def export_all_journals(
    request: Request,
    search: str = "",
    status: str = "",
    start_date: str = "",
    end_date: str = "",
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Export all journal entries matching filters to CSV."""
    return await gl_web_service.export_all_journals_response(
        auth, db, search, status, start_date, end_date
    )


@router.post("/journals/export")
def queue_journals_export(
    search: str = "",
    status: str = "",
    start_date: str = "",
    end_date: str = "",
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> JSONResponse:
    """Queue all journal entries matching filters for CSV export."""
    instance = queue_background_export(
        db,
        auth.organization_id,
        auth.user_id,
        report_code="GL_JOURNALS",
        parameters={
            "search": search,
            "status": status,
            "start_date": start_date,
            "end_date": end_date,
        },
        output_format="CSV",
    )
    return JSONResponse(
        {
            "message": "GL Journals export is processing. You will be notified when it is ready.",
            "instance_id": str(instance.instance_id),
            "status_url": f"/finance/gl/journals/exports/{instance.instance_id}/status",
        },
        status_code=202,
    )


@router.get("/journals/exports/{instance_id}/download")
def download_journals_export(
    instance_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> Response:
    """Download a completed queued GL Journals export."""
    body, filename, media_type, content_length = get_completed_export_for_download(
        db,
        auth.organization_id,
        auth.user_id,
        instance_id,
        report_code="GL_JOURNALS",
    )
    if hasattr(body, "__fspath__"):
        return FileResponse(body, filename=filename, media_type=media_type)

    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    return StreamingResponse(body, media_type=media_type, headers=headers)


@router.get("/journals/exports/{instance_id}/status")
def journals_export_status(
    instance_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> JSONResponse:
    """Return the status of a queued GL Journals export."""
    return JSONResponse(
        get_export_status(
            db,
            auth.organization_id,
            auth.user_id,
            instance_id,
            report_code="GL_JOURNALS",
        )
    )


@router.get("/journals/{entry_id}", response_class=HTMLResponse)
def view_journal(
    request: Request,
    entry_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Journal entry detail page."""
    return gl_web_service.journal_detail_response(request, auth, db, entry_id)


@router.get("/journals/{entry_id}/edit", response_class=HTMLResponse)
def edit_journal_form(
    request: Request,
    entry_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit journal entry form page."""
    return gl_web_service.journal_edit_form_response(request, auth, db, entry_id)


@router.post("/journals/new")
def create_journal(
    request: Request,
    journal_type: str = Form(...),
    fiscal_period_id: str = Form(...),
    entry_date: str = Form(...),
    posting_date: str = Form(...),
    description: str = Form(...),
    reference: str = Form(""),
    currency_code: str | None = Form(None),
    exchange_rate: str = Form("1.0"),
    lines_json: str = Form("[]"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Create a new journal entry."""
    return gl_web_service.create_journal_response(
        request,
        auth,
        db,
        journal_type,
        fiscal_period_id,
        entry_date,
        posting_date,
        description,
        reference,
        currency_code,
        exchange_rate,
        lines_json,
    )


@router.post("/journals/{entry_id}/edit")
def update_journal(
    request: Request,
    entry_id: str,
    journal_type: str = Form(...),
    fiscal_period_id: str = Form(...),
    entry_date: str = Form(...),
    posting_date: str = Form(...),
    description: str = Form(...),
    reference: str = Form(""),
    currency_code: str | None = Form(None),
    exchange_rate: str = Form("1.0"),
    lines_json: str = Form("[]"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Update an existing journal entry."""
    return gl_web_service.update_journal_response(
        request,
        auth,
        db,
        entry_id,
        journal_type,
        fiscal_period_id,
        entry_date,
        posting_date,
        description,
        reference,
        currency_code,
        exchange_rate,
        lines_json,
    )


@router.post("/journals/{entry_id}/delete")
def delete_journal(
    request: Request,
    entry_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Delete a journal entry."""
    return gl_web_service.delete_journal_response(request, auth, db, entry_id)


@router.post("/journals/{entry_id}/post")
def post_journal(
    request: Request,
    entry_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Post a journal entry to the general ledger."""
    return gl_web_service.post_journal_response(request, auth, db, entry_id)


@router.post("/journals/{entry_id}/reverse")
def reverse_journal(
    request: Request,
    entry_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Reverse a posted journal entry."""
    return gl_web_service.reverse_journal_response(request, auth, db, entry_id)


# =============================================================================
# Bulk Actions - Journals
# =============================================================================


@router.post("/journals/bulk-delete")
async def bulk_delete_journals(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Bulk delete journal entries (only DRAFT status)."""
    return await gl_web_service.bulk_delete_journals_response(request, auth, db)


@router.post("/journals/bulk-export")
async def bulk_export_journals(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Export selected journal entries to CSV."""
    return await gl_web_service.bulk_export_journals_response(request, auth, db)


@router.post("/journals/bulk-approve")
async def bulk_approve_journals(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Bulk approve journal entries (from DRAFT status)."""
    return await gl_web_service.bulk_approve_journals_response(request, auth, db)


@router.post("/journals/bulk-post")
async def bulk_post_journals(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Bulk post journal entries to ledger."""
    return await gl_web_service.bulk_post_journals_response(request, auth, db)


@router.get("/period-close", response_class=HTMLResponse)
def period_close(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
):
    """Period close checklist page."""
    return gl_web_service.period_close_response(request, auth)


@router.get("/periods", response_class=HTMLResponse)
def list_periods(
    request: Request,
    year_id: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Fiscal periods list page."""
    return gl_web_service.list_periods_response(request, auth, db, year_id=year_id)


@router.get("/periods/new", response_class=HTMLResponse)
def new_period_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """New fiscal period form page."""
    return gl_web_service.new_period_form_response(request, auth, db)


@router.post("/periods/new")
@router.post("/periods/new/", include_in_schema=False)
def create_period(
    request: Request,
    fiscal_year_id: str = Form(...),
    period_number: int = Form(...),
    period_name: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    is_adjustment_period: str | None = Form(None),
    is_closing_period: str | None = Form(None),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Create a new fiscal period."""
    return gl_web_service.create_period_response(
        request=request,
        auth=auth,
        db=db,
        fiscal_year_id=fiscal_year_id,
        period_number=period_number,
        period_name=period_name,
        start_date=start_date,
        end_date=end_date,
        is_adjustment_period=is_adjustment_period is not None,
        is_closing_period=is_closing_period is not None,
    )


@router.post("/periods/{period_id}/open")
@router.post("/periods/{period_id}/open/", include_in_schema=False)
def open_period(
    request: Request,
    period_id: str,
    year_id: str | None = Form(default=None),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Open (or reopen) a fiscal period."""
    return gl_web_service.open_period_response(
        request=request,
        auth=auth,
        db=db,
        period_id=period_id,
        year_id=year_id,
    )


@router.get("/periods/{period_id}/open", include_in_schema=False)
@router.get("/periods/{period_id}/open/", include_in_schema=False)
def open_period_legacy_get(
    period_id: str,
    year_id: str | None = None,
):
    """Legacy GET endpoint kept for backward compatibility.

    State-changing actions must use POST with CSRF protection.
    """
    url = "/finance/gl/periods?error=Use+the+Open+button+to+submit+this+action"
    if year_id:
        url = (
            f"/finance/gl/periods?year_id={year_id}"
            "&error=Use+the+Open+button+to+submit+this+action"
        )
    return RedirectResponse(url=url, status_code=303)


@router.post("/periods/{period_id}/close")
@router.post("/periods/{period_id}/close/", include_in_schema=False)
def close_period(
    request: Request,
    period_id: str,
    year_id: str | None = Form(default=None),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Soft-close a fiscal period."""
    return gl_web_service.close_period_response(
        request=request,
        auth=auth,
        db=db,
        period_id=period_id,
        year_id=year_id,
    )


@router.get("/periods/{period_id}/close", include_in_schema=False)
@router.get("/periods/{period_id}/close/", include_in_schema=False)
def close_period_legacy_get(
    period_id: str,
    year_id: str | None = None,
):
    """Legacy GET endpoint kept for backward compatibility.

    State-changing actions must use POST with CSRF protection.
    """
    url = "/finance/gl/periods?error=Use+the+Close+button+to+submit+this+action"
    if year_id:
        url = (
            f"/finance/gl/periods?year_id={year_id}"
            "&error=Use+the+Close+button+to+submit+this+action"
        )
    return RedirectResponse(url=url, status_code=303)


@router.get("/trial-balance", response_class=HTMLResponse)
def trial_balance(
    request: Request,
    as_of_date: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Trial balance report page."""
    return gl_web_service.trial_balance_response(request, auth, db, as_of_date)


@router.get(
    "/periods/{period_id}/fx-revaluation",
    response_class=HTMLResponse,
)
def period_fx_revaluation_preview(
    request: Request,
    period_id: str,
    auth: WebAuthContext = Depends(require_finance_admin),
    db: Session = Depends(get_db_for_org),
):
    """Render the FX revaluation preview page for a fiscal period.

    Read-only: shows per-(account, currency) revaluation observations,
    closing rates used, warnings, and totals so the user can review before
    posting.
    """
    ws = FXRevaluationWebService(db)
    context = base_context(request, auth, "FX Revaluation Preview", "gl")
    context.update(
        ws.preview_response(
            coerce_uuid(auth.organization_id),
            coerce_uuid(period_id),
        )
    )
    return templates.TemplateResponse(
        request, "finance/gl/period_fx_revaluation.html", context
    )


@router.post("/periods/{period_id}/fx-revaluation")
@router.post("/periods/{period_id}/fx-revaluation/", include_in_schema=False)
def period_fx_revaluation_post(
    request: Request,
    period_id: str,
    reason: str | None = Form(default=None),
    auth: WebAuthContext = Depends(require_finance_admin),
    db: Session = Depends(get_db_for_org),
):
    """Post the period-end FX revaluation pair (period-end + reversal)."""
    ws = FXRevaluationWebService(db)
    result = ws.post_response(
        coerce_uuid(auth.organization_id),
        coerce_uuid(period_id),
        coerce_uuid(auth.person_id),
        reason,
    )
    db.commit()
    msg = quote_plus(result.message or "FX revaluation posted.")
    return RedirectResponse(
        url=f"/finance/gl/periods?msg={msg}",
        status_code=303,
    )
