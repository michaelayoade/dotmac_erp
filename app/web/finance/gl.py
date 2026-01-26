"""
GL (General Ledger) Web Routes.

HTML template routes for Chart of Accounts, Journal Entries, and Fiscal Periods.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.finance.gl.web import gl_web_service
from app.web.deps import get_db, require_finance_access, WebAuthContext


router = APIRouter(prefix="/gl", tags=["gl-web"])


@router.get("/accounts", response_class=HTMLResponse)
def list_accounts(
    request: Request,
    search: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
    )


@router.get("/accounts/new", response_class=HTMLResponse)
def new_account_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New account form page."""
    return gl_web_service.account_new_form_response(request, auth, db)


@router.get("/accounts/suggest-code/{category_id}")
def suggest_account_code(
    category_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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


@router.get("/accounts/{account_id}", response_class=HTMLResponse)
def view_account(
    request: Request,
    account_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Account detail page."""
    return gl_web_service.account_detail_response(request, auth, db, account_id)


@router.get("/accounts/{account_id}/edit", response_class=HTMLResponse)
def edit_account_form(
    request: Request,
    account_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
    is_multi_currency: Optional[str] = Form(None),
    default_currency_code: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
    is_posting_allowed: Optional[str] = Form(None),
    is_budgetable: Optional[str] = Form(None),
    is_reconciliation_required: Optional[str] = Form(None),
    subledger_type: Optional[str] = Form(None),
    is_cash_equivalent: Optional[str] = Form(None),
    is_financial_instrument: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
    is_multi_currency: Optional[str] = Form(None),
    default_currency_code: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
    is_posting_allowed: Optional[str] = Form(None),
    is_budgetable: Optional[str] = Form(None),
    is_reconciliation_required: Optional[str] = Form(None),
    subledger_type: Optional[str] = Form(None),
    is_cash_equivalent: Optional[str] = Form(None),
    is_financial_instrument: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
):
    """Bulk delete accounts (if no journal entries)."""
    return await gl_web_service.bulk_delete_accounts_response(request, auth, db)


@router.post("/accounts/bulk-export")
async def bulk_export_accounts(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Export selected accounts to CSV."""
    return await gl_web_service.bulk_export_accounts_response(request, auth, db)


@router.post("/accounts/bulk-activate")
async def bulk_activate_accounts(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Bulk activate accounts."""
    return await gl_web_service.bulk_activate_accounts_response(request, auth, db)


@router.post("/accounts/bulk-deactivate")
async def bulk_deactivate_accounts(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Bulk deactivate accounts."""
    return await gl_web_service.bulk_deactivate_accounts_response(request, auth, db)


# ═══════════════════════════════════════════════════════════════════
# Ledger Transactions
# ═══════════════════════════════════════════════════════════════════


@router.get("/ledger", response_class=HTMLResponse)
def list_ledger(
    request: Request,
    account_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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


@router.get("/journals", response_class=HTMLResponse)
def list_journals(
    request: Request,
    search: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
    )


@router.get("/journals/new", response_class=HTMLResponse)
def new_journal_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New journal entry form page."""
    return gl_web_service.journal_new_form_response(request, auth, db)


@router.get("/journals/{entry_id}", response_class=HTMLResponse)
def view_journal(
    request: Request,
    entry_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Journal entry detail page."""
    return gl_web_service.journal_detail_response(request, auth, db, entry_id)


@router.get("/journals/{entry_id}/edit", response_class=HTMLResponse)
def edit_journal_form(
    request: Request,
    entry_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
    currency_code: Optional[str] = Form(None),
    exchange_rate: str = Form("1.0"),
    lines_json: str = Form("[]"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
    currency_code: Optional[str] = Form(None),
    exchange_rate: str = Form("1.0"),
    lines_json: str = Form("[]"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
):
    """Delete a journal entry."""
    return gl_web_service.delete_journal_response(request, auth, db, entry_id)


@router.post("/journals/{entry_id}/post")
def post_journal(
    request: Request,
    entry_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Post a journal entry to the general ledger."""
    return gl_web_service.post_journal_response(request, auth, db, entry_id)


@router.post("/journals/{entry_id}/reverse")
def reverse_journal(
    request: Request,
    entry_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
):
    """Bulk delete journal entries (only DRAFT status)."""
    return await gl_web_service.bulk_delete_journals_response(request, auth, db)


@router.post("/journals/bulk-export")
async def bulk_export_journals(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Export selected journal entries to CSV."""
    return await gl_web_service.bulk_export_journals_response(request, auth, db)


@router.post("/journals/bulk-approve")
async def bulk_approve_journals(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Bulk approve journal entries (from DRAFT status)."""
    return await gl_web_service.bulk_approve_journals_response(request, auth, db)


@router.post("/journals/bulk-post")
async def bulk_post_journals(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Fiscal periods list page."""
    return gl_web_service.list_periods_response(request, auth, db)


@router.get("/periods/new", response_class=HTMLResponse)
def new_period_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New fiscal period form page."""
    return gl_web_service.new_period_form_response(request, auth, db)


@router.get("/trial-balance", response_class=HTMLResponse)
def trial_balance(
    request: Request,
    as_of_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Trial balance report page."""
    return gl_web_service.trial_balance_response(request, auth, db, as_of_date)
