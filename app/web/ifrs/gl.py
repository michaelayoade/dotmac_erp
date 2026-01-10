"""
GL (General Ledger) Web Routes.

HTML template routes for Chart of Accounts, Journal Entries, and Fiscal Periods.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.web.deps import get_db, require_web_auth, WebAuthContext, base_context
from app.services.ifrs.gl.web import gl_web_service

templates = Jinja2Templates(directory="templates")

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
    return templates.TemplateResponse(request, "ifrs/gl/account_form.html", context)


@router.get("/accounts/{account_id}", response_class=HTMLResponse)
def view_account(
    request: Request,
    account_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Account detail page."""
    account = None

    context = base_context(request, auth, "Account Details", "gl")
    context["account"] = account

    return templates.TemplateResponse(request, "ifrs/gl/account_detail.html", context)


@router.get("/accounts/{account_id}/edit", response_class=HTMLResponse)
def edit_account_form(
    request: Request,
    account_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Edit account form page."""
    account = None

    context = base_context(request, auth, "Edit Account", "gl")
    context["account"] = account

    return templates.TemplateResponse(request, "ifrs/gl/account_form.html", context)


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
    return templates.TemplateResponse(request, "ifrs/gl/journal_form.html", context)


@router.get("/journals/{entry_id}", response_class=HTMLResponse)
def view_journal(
    request: Request,
    entry_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Journal entry detail page."""
    entry = None

    context = base_context(request, auth, "Journal Entry Details", "gl")
    context["entry"] = entry

    return templates.TemplateResponse(request, "ifrs/gl/journal_detail.html", context)


@router.get("/journals/{entry_id}/edit", response_class=HTMLResponse)
def edit_journal_form(
    request: Request,
    entry_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Edit journal entry form page."""
    entry = None

    context = base_context(request, auth, "Edit Journal Entry", "gl")
    context["entry"] = entry

    return templates.TemplateResponse(request, "ifrs/gl/journal_form.html", context)


# =============================================================================
# Fiscal Periods
# =============================================================================

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
