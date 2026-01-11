"""
Banking Web Routes.

HTML template routes for Bank Accounts, Statements, and Reconciliations.
"""

from typing import Optional
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.web.deps import get_db, require_web_auth, WebAuthContext, base_context
from app.services.ifrs.banking.web import banking_web_service

templates = Jinja2Templates(directory="templates")

router = APIRouter(prefix="/banking", tags=["banking-web"])


# =============================================================================
# Bank Accounts
# =============================================================================

@router.get("/accounts", response_class=HTMLResponse)
def list_bank_accounts(
    request: Request,
    search: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Bank accounts list page."""
    context = base_context(request, auth, "Bank Accounts", "banking")
    context.update(
        banking_web_service.list_accounts_context(
            db,
            str(auth.organization_id),
            search=search,
            status=status,
            page=page,
        )
    )

    return templates.TemplateResponse(request, "ifrs/banking/accounts.html", context)


@router.get("/accounts/new", response_class=HTMLResponse)
def new_bank_account_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New bank account form page."""
    context = base_context(request, auth, "New Bank Account", "banking")
    context.update(
        banking_web_service.account_form_context(db, str(auth.organization_id))
    )
    return templates.TemplateResponse(request, "ifrs/banking/account_form.html", context)


@router.get("/accounts/{account_id}", response_class=HTMLResponse)
def view_bank_account(
    request: Request,
    account_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Bank account detail page."""
    context = base_context(request, auth, "Bank Account Details", "banking")
    context.update(
        banking_web_service.account_detail_context(
            db, str(auth.organization_id), account_id
        )
    )

    return templates.TemplateResponse(request, "ifrs/banking/account_detail.html", context)


@router.get("/accounts/{account_id}/edit", response_class=HTMLResponse)
def edit_bank_account_form(
    request: Request,
    account_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Edit bank account form page."""
    context = base_context(request, auth, "Edit Bank Account", "banking")
    context.update(
        banking_web_service.account_form_context(
            db, str(auth.organization_id), account_id
        )
    )

    return templates.TemplateResponse(request, "ifrs/banking/account_form.html", context)


# =============================================================================
# Bank Statements
# =============================================================================

@router.get("/statements", response_class=HTMLResponse)
def list_statements(
    request: Request,
    account_id: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Bank statements list page."""
    context = base_context(request, auth, "Bank Statements", "banking")
    context.update(
        banking_web_service.list_statements_context(
            db,
            str(auth.organization_id),
            account_id=account_id,
            status=status,
            start_date=start_date,
            end_date=end_date,
            page=page,
        )
    )

    return templates.TemplateResponse(request, "ifrs/banking/statements.html", context)


@router.get("/statements/import", response_class=HTMLResponse)
def import_statement_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Import statement form page."""
    context = base_context(request, auth, "Import Bank Statement", "banking")
    context.update(
        banking_web_service.statement_import_context(db, str(auth.organization_id))
    )

    return templates.TemplateResponse(request, "ifrs/banking/statement_import.html", context)


@router.get("/statements/{statement_id}", response_class=HTMLResponse)
def view_statement(
    request: Request,
    statement_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Statement detail page with lines."""
    context = base_context(request, auth, "Bank Statement", "banking")
    context.update(
        banking_web_service.statement_detail_context(
            db, str(auth.organization_id), statement_id
        )
    )

    return templates.TemplateResponse(request, "ifrs/banking/statement_detail.html", context)


# =============================================================================
# Bank Reconciliations
# =============================================================================

@router.get("/reconciliations", response_class=HTMLResponse)
def list_reconciliations(
    request: Request,
    account_id: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Reconciliations list page."""
    context = base_context(request, auth, "Bank Reconciliations", "banking")
    context.update(
        banking_web_service.list_reconciliations_context(
            db,
            str(auth.organization_id),
            account_id=account_id,
            status=status,
            start_date=start_date,
            end_date=end_date,
            page=page,
        )
    )

    return templates.TemplateResponse(request, "ifrs/banking/reconciliations.html", context)


@router.get("/reconciliations/new", response_class=HTMLResponse)
def new_reconciliation_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New reconciliation form page."""
    context = base_context(request, auth, "New Reconciliation", "banking")
    context.update(
        banking_web_service.reconciliation_form_context(
            db, str(auth.organization_id)
        )
    )

    return templates.TemplateResponse(request, "ifrs/banking/reconciliation_form.html", context)


@router.get("/reconciliations/{reconciliation_id}", response_class=HTMLResponse)
def view_reconciliation(
    request: Request,
    reconciliation_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Reconciliation workspace page."""
    context = base_context(request, auth, "Bank Reconciliation", "banking")
    context.update(
        banking_web_service.reconciliation_detail_context(
            db, str(auth.organization_id), reconciliation_id
        )
    )

    return templates.TemplateResponse(request, "ifrs/banking/reconciliation.html", context)


@router.get("/reconciliations/{reconciliation_id}/report", response_class=HTMLResponse)
def reconciliation_report(
    request: Request,
    reconciliation_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Reconciliation report page (printable)."""
    context = base_context(request, auth, "Reconciliation Report", "banking")
    context.update(
        banking_web_service.reconciliation_report_context(
            db, str(auth.organization_id), reconciliation_id
        )
    )

    return templates.TemplateResponse(request, "ifrs/banking/reconciliation_report.html", context)


# =============================================================================
# Payees
# =============================================================================

@router.get("/payees", response_class=HTMLResponse)
def list_payees(
    request: Request,
    search: Optional[str] = None,
    payee_type: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Payees list page."""
    context = base_context(request, auth, "Payees", "banking")
    context.update(
        banking_web_service.list_payees_context(
            db,
            str(auth.organization_id),
            search=search,
            payee_type=payee_type,
            page=page,
        )
    )

    return templates.TemplateResponse(request, "ifrs/banking/payees.html", context)


@router.get("/payees/new", response_class=HTMLResponse)
def new_payee_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New payee form page."""
    context = base_context(request, auth, "New Payee", "banking")
    context.update(
        banking_web_service.payee_form_context(db, str(auth.organization_id))
    )
    return templates.TemplateResponse(request, "ifrs/banking/payee_form.html", context)


@router.get("/payees/{payee_id}", response_class=HTMLResponse)
def view_payee(
    request: Request,
    payee_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Payee detail/edit page."""
    context = base_context(request, auth, "Edit Payee", "banking")
    context.update(
        banking_web_service.payee_form_context(
            db, str(auth.organization_id), payee_id=payee_id
        )
    )

    return templates.TemplateResponse(request, "ifrs/banking/payee_form.html", context)


# =============================================================================
# Transaction Rules
# =============================================================================

@router.get("/rules", response_class=HTMLResponse)
def list_transaction_rules(
    request: Request,
    rule_type: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Transaction rules list page."""
    context = base_context(request, auth, "Transaction Rules", "banking")
    context.update(
        banking_web_service.list_rules_context(
            db,
            str(auth.organization_id),
            rule_type=rule_type,
            page=page,
        )
    )

    return templates.TemplateResponse(request, "ifrs/banking/rules.html", context)


@router.get("/rules/new", response_class=HTMLResponse)
def new_rule_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New transaction rule form page."""
    context = base_context(request, auth, "New Transaction Rule", "banking")
    context.update(
        banking_web_service.rule_form_context(db, str(auth.organization_id))
    )
    return templates.TemplateResponse(request, "ifrs/banking/rule_form.html", context)


@router.get("/rules/{rule_id}", response_class=HTMLResponse)
def view_rule(
    request: Request,
    rule_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Transaction rule detail/edit page."""
    context = base_context(request, auth, "Edit Transaction Rule", "banking")
    context.update(
        banking_web_service.rule_form_context(
            db, str(auth.organization_id), rule_id=rule_id
        )
    )

    return templates.TemplateResponse(request, "ifrs/banking/rule_form.html", context)
