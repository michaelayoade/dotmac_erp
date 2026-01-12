"""
GL (General Ledger) Web Routes.

HTML template routes for Chart of Accounts, Journal Entries, and Fiscal Periods.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.ifrs.gl.web import gl_web_service
from app.services.ifrs.platform.org_context import org_context_service
from app.templates import templates
from app.web.deps import get_db, require_web_auth, WebAuthContext, base_context


router = APIRouter(prefix="/gl", tags=["gl-web"])


# =============================================================================
# Chart of Accounts
# =============================================================================

@router.get("/accounts", response_class=HTMLResponse)
def list_accounts(
    request: Request,
    search: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Chart of Accounts list page."""
    context = base_context(request, auth, "Chart of Accounts", "gl")
    context.update(
        gl_web_service.list_accounts_context(
            db,
            str(auth.organization_id),
            search=search,
            category=category,
            status=status,
            page=page,
        )
    )
    return templates.TemplateResponse(request, "ifrs/gl/accounts.html", context)


@router.get("/accounts/new", response_class=HTMLResponse)
def new_account_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New account form page."""
    context = base_context(request, auth, "New Account", "gl")
    context.update(gl_web_service.account_form_context(db, str(auth.organization_id)))
    return templates.TemplateResponse(request, "ifrs/gl/account_form.html", context)


@router.get("/accounts/{account_id}", response_class=HTMLResponse)
def view_account(
    request: Request,
    account_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Account detail page."""
    context = base_context(request, auth, "Account Details", "gl")
    context.update(
        gl_web_service.account_detail_context(
            db,
            str(auth.organization_id),
            account_id,
        )
    )

    return templates.TemplateResponse(request, "ifrs/gl/account_detail.html", context)


@router.get("/accounts/{account_id}/edit", response_class=HTMLResponse)
def edit_account_form(
    request: Request,
    account_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Edit account form page."""
    context = base_context(request, auth, "Edit Account", "gl")
    context.update(
        gl_web_service.account_form_context(
            db,
            str(auth.organization_id),
            account_id=account_id,
        )
    )

    return templates.TemplateResponse(request, "ifrs/gl/account_form.html", context)


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
    is_multi_currency: bool = Form(False),
    default_currency_code: Optional[str] = Form(None),
    is_active: bool = Form(True),
    is_posting_allowed: bool = Form(True),
    is_budgetable: bool = Form(False),
    is_reconciliation_required: bool = Form(False),
    subledger_type: Optional[str] = Form(None),
    is_cash_equivalent: bool = Form(False),
    is_financial_instrument: bool = Form(False),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Create a new GL account."""
    if not default_currency_code:
        default_currency_code = org_context_service.get_functional_currency(
            db,
            auth.organization_id,
        )

    account, error = gl_web_service.create_account(
        db,
        str(auth.organization_id),
        account_code=account_code,
        account_name=account_name,
        category_id=category_id,
        account_type=account_type,
        normal_balance=normal_balance,
        description=description,
        search_terms=search_terms,
        is_multi_currency=is_multi_currency,
        default_currency_code=default_currency_code,
        is_active=is_active,
        is_posting_allowed=is_posting_allowed,
        is_budgetable=is_budgetable,
        is_reconciliation_required=is_reconciliation_required,
        subledger_type=subledger_type,
        is_cash_equivalent=is_cash_equivalent,
        is_financial_instrument=is_financial_instrument,
    )

    if error or account is None:
        context = base_context(request, auth, "New Account", "gl")
        context.update(gl_web_service.account_form_context(db, str(auth.organization_id)))
        context["error"] = error or "Account creation failed"
        context["form_data"] = {
            "account_code": account_code,
            "account_name": account_name,
            "category_id": category_id,
            "account_type": account_type,
            "normal_balance": normal_balance,
            "description": description,
            "search_terms": search_terms,
            "is_multi_currency": is_multi_currency,
            "default_currency_code": default_currency_code,
            "is_active": is_active,
            "is_posting_allowed": is_posting_allowed,
            "is_budgetable": is_budgetable,
            "is_reconciliation_required": is_reconciliation_required,
            "subledger_type": subledger_type,
            "is_cash_equivalent": is_cash_equivalent,
            "is_financial_instrument": is_financial_instrument,
        }
        return templates.TemplateResponse(request, "ifrs/gl/account_form.html", context)

    return RedirectResponse(url=f"/gl/accounts/{account.account_id}", status_code=303)


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
    is_multi_currency: bool = Form(False),
    default_currency_code: Optional[str] = Form(None),
    is_active: bool = Form(True),
    is_posting_allowed: bool = Form(True),
    is_budgetable: bool = Form(False),
    is_reconciliation_required: bool = Form(False),
    subledger_type: Optional[str] = Form(None),
    is_cash_equivalent: bool = Form(False),
    is_financial_instrument: bool = Form(False),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Update an existing GL account."""
    if not default_currency_code:
        default_currency_code = org_context_service.get_functional_currency(
            db,
            auth.organization_id,
        )

    account, error = gl_web_service.update_account(
        db,
        str(auth.organization_id),
        account_id=account_id,
        account_code=account_code,
        account_name=account_name,
        category_id=category_id,
        account_type=account_type,
        normal_balance=normal_balance,
        description=description,
        search_terms=search_terms,
        is_multi_currency=is_multi_currency,
        default_currency_code=default_currency_code,
        is_active=is_active,
        is_posting_allowed=is_posting_allowed,
        is_budgetable=is_budgetable,
        is_reconciliation_required=is_reconciliation_required,
        subledger_type=subledger_type,
        is_cash_equivalent=is_cash_equivalent,
        is_financial_instrument=is_financial_instrument,
    )

    if error:
        context = base_context(request, auth, "Edit Account", "gl")
        context.update(
            gl_web_service.account_form_context(
                db, str(auth.organization_id), account_id=account_id
            )
        )
        context["error"] = error
        return templates.TemplateResponse(request, "ifrs/gl/account_form.html", context)

    return RedirectResponse(url=f"/gl/accounts/{account_id}", status_code=303)


@router.post("/accounts/{account_id}/delete")
def delete_account(
    request: Request,
    account_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Delete a GL account."""
    error = gl_web_service.delete_account(db, str(auth.organization_id), account_id)

    if error:
        context = base_context(request, auth, "Account Details", "gl")
        context.update(
            gl_web_service.account_detail_context(
                db, str(auth.organization_id), account_id
            )
        )
        context["error"] = error
        return templates.TemplateResponse(request, "ifrs/gl/account_detail.html", context)

    return RedirectResponse(url="/gl/accounts", status_code=303)


# =============================================================================
# Journal Entries
# =============================================================================

@router.get("/journals", response_class=HTMLResponse)
def list_journals(
    request: Request,
    search: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Journal entries list page."""
    context = base_context(request, auth, "Journal Entries", "gl")
    context.update(
        gl_web_service.list_journals_context(
            db,
            str(auth.organization_id),
            search=search,
            status=status,
            start_date=start_date,
            end_date=end_date,
            page=page,
        )
    )
    return templates.TemplateResponse(request, "ifrs/gl/journals.html", context)


@router.get("/journals/new", response_class=HTMLResponse)
def new_journal_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New journal entry form page."""
    context = base_context(request, auth, "New Journal Entry", "gl")
    context.update(gl_web_service.journal_form_context(db, str(auth.organization_id)))
    return templates.TemplateResponse(request, "ifrs/gl/journal_form.html", context)


@router.get("/journals/{entry_id}", response_class=HTMLResponse)
def view_journal(
    request: Request,
    entry_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Journal entry detail page."""
    context = base_context(request, auth, "Journal Entry Details", "gl")
    context.update(
        gl_web_service.journal_detail_context(
            db,
            str(auth.organization_id),
            entry_id,
        )
    )

    return templates.TemplateResponse(request, "ifrs/gl/journal_detail.html", context)


@router.get("/journals/{entry_id}/edit", response_class=HTMLResponse)
def edit_journal_form(
    request: Request,
    entry_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Edit journal entry form page."""
    context = base_context(request, auth, "Edit Journal Entry", "gl")
    context.update(
        gl_web_service.journal_form_context(
            db,
            str(auth.organization_id),
            entry_id=entry_id,
        )
    )

    return templates.TemplateResponse(request, "ifrs/gl/journal_form.html", context)


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
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Create a new journal entry."""
    if not currency_code:
        currency_code = org_context_service.get_functional_currency(
            db,
            auth.organization_id,
        )

    entry, error = gl_web_service.create_journal(
        db,
        str(auth.organization_id),
        str(auth.user_id),
        journal_type=journal_type,
        fiscal_period_id=fiscal_period_id,
        entry_date=entry_date,
        posting_date=posting_date,
        description=description,
        reference=reference,
        currency_code=currency_code,
        exchange_rate=exchange_rate,
        lines_json=lines_json,
    )

    if error or entry is None:
        context = base_context(request, auth, "New Journal Entry", "gl")
        context.update(gl_web_service.journal_form_context(db, str(auth.organization_id)))
        context["error"] = error or "Journal entry creation failed"
        context["form_data"] = {
            "journal_type": journal_type,
            "fiscal_period_id": fiscal_period_id,
            "entry_date": entry_date,
            "posting_date": posting_date,
            "description": description,
            "reference": reference,
            "currency_code": currency_code,
            "exchange_rate": exchange_rate,
            "lines_json": lines_json,
        }
        return templates.TemplateResponse(request, "ifrs/gl/journal_form.html", context)

    return RedirectResponse(url=f"/gl/journals/{entry.journal_entry_id}", status_code=303)


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
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Update an existing journal entry."""
    if not currency_code:
        currency_code = org_context_service.get_functional_currency(
            db,
            auth.organization_id,
        )

    entry, error = gl_web_service.update_journal(
        db,
        str(auth.organization_id),
        entry_id=entry_id,
        journal_type=journal_type,
        fiscal_period_id=fiscal_period_id,
        entry_date=entry_date,
        posting_date=posting_date,
        description=description,
        reference=reference,
        currency_code=currency_code,
        exchange_rate=exchange_rate,
        lines_json=lines_json,
    )

    if error:
        context = base_context(request, auth, "Edit Journal Entry", "gl")
        context.update(
            gl_web_service.journal_form_context(
                db, str(auth.organization_id), entry_id=entry_id
            )
        )
        context["error"] = error
        return templates.TemplateResponse(request, "ifrs/gl/journal_form.html", context)

    return RedirectResponse(url=f"/gl/journals/{entry_id}", status_code=303)


@router.post("/journals/{entry_id}/delete")
def delete_journal(
    request: Request,
    entry_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Delete a journal entry."""
    error = gl_web_service.delete_journal(db, str(auth.organization_id), entry_id)

    if error:
        context = base_context(request, auth, "Journal Entry Details", "gl")
        context.update(
            gl_web_service.journal_detail_context(
                db, str(auth.organization_id), entry_id
            )
        )
        context["error"] = error
        return templates.TemplateResponse(request, "ifrs/gl/journal_detail.html", context)

    return RedirectResponse(url="/gl/journals", status_code=303)


# =============================================================================
# Fiscal Periods
# =============================================================================

@router.get("/period-close", response_class=HTMLResponse)
def period_close(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
):
    """Period close checklist page."""
    context = base_context(request, auth, "Period Close", "gl")
    return templates.TemplateResponse(request, "ifrs/gl/period_close.html", context)

@router.get("/periods", response_class=HTMLResponse)
def list_periods(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Fiscal periods list page."""
    context = base_context(request, auth, "Fiscal Periods", "gl")
    context.update(gl_web_service.periods_context(db, str(auth.organization_id)))
    return templates.TemplateResponse(request, "ifrs/gl/periods.html", context)


@router.get("/periods/new", response_class=HTMLResponse)
def new_period_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New fiscal period form page."""
    context = base_context(request, auth, "New Fiscal Year", "gl")
    context.update(gl_web_service.period_form_context(db, str(auth.organization_id)))
    return templates.TemplateResponse(request, "ifrs/gl/period_form.html", context)


# =============================================================================
# Trial Balance
# =============================================================================

@router.get("/trial-balance", response_class=HTMLResponse)
def trial_balance(
    request: Request,
    as_of_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Trial balance report page."""
    context = base_context(request, auth, "Trial Balance", "gl")
    context.update(
        gl_web_service.trial_balance_context(
            db,
            str(auth.organization_id),
            as_of_date=as_of_date,
        )
    )
    return templates.TemplateResponse(request, "ifrs/gl/trial_balance.html", context)
