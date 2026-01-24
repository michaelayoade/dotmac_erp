"""
Banking Web Routes.

HTML template routes for Bank Accounts, Statements, and Reconciliations.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.finance.banking.web import banking_web_service
from app.web.deps import get_db, require_finance_access, WebAuthContext


router = APIRouter(prefix="/banking", tags=["banking-web"])


@router.get("/accounts", response_class=HTMLResponse)
def list_bank_accounts(
    request: Request,
    search: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Bank accounts list page."""
    return banking_web_service.list_accounts_response(request, auth, db, search, status, page)


@router.get("/accounts/new", response_class=HTMLResponse)
def new_bank_account_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New bank account form page."""
    return banking_web_service.account_new_form_response(request, auth, db)


@router.get("/accounts/{account_id}", response_class=HTMLResponse)
def view_bank_account(
    request: Request,
    account_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Bank account detail page."""
    return banking_web_service.account_detail_response(request, auth, db, account_id)


@router.get("/accounts/{account_id}/edit", response_class=HTMLResponse)
def edit_bank_account_form(
    request: Request,
    account_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Edit bank account form page."""
    return banking_web_service.account_edit_form_response(request, auth, db, account_id)


@router.get("/statements", response_class=HTMLResponse)
def list_statements(
    request: Request,
    account_id: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Bank statements list page."""
    return banking_web_service.list_statements_response(
        request,
        auth,
        db,
        account_id,
        status,
        start_date,
        end_date,
        page,
    )


@router.get("/statements/import", response_class=HTMLResponse)
def import_statement_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Import statement form page."""
    return banking_web_service.statement_import_form_response(request, auth, db)


@router.get("/statements/{statement_id}", response_class=HTMLResponse)
def view_statement(
    request: Request,
    statement_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Statement detail page with lines."""
    return banking_web_service.statement_detail_response(request, auth, db, statement_id)


@router.get("/reconciliations", response_class=HTMLResponse)
def list_reconciliations(
    request: Request,
    account_id: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Reconciliations list page."""
    return banking_web_service.list_reconciliations_response(
        request,
        auth,
        db,
        account_id,
        status,
        start_date,
        end_date,
        page,
    )


@router.get("/reconciliations/new", response_class=HTMLResponse)
def new_reconciliation_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New reconciliation form page."""
    return banking_web_service.reconciliation_new_form_response(request, auth, db)


@router.get("/reconciliations/{reconciliation_id}", response_class=HTMLResponse)
def view_reconciliation(
    request: Request,
    reconciliation_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Reconciliation workspace page."""
    return banking_web_service.reconciliation_detail_response(request, auth, db, reconciliation_id)


@router.get("/reconciliations/{reconciliation_id}/report", response_class=HTMLResponse)
def reconciliation_report(
    request: Request,
    reconciliation_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Reconciliation report page (printable)."""
    return banking_web_service.reconciliation_report_response(
        request,
        auth,
        db,
        reconciliation_id,
    )


@router.get("/payees", response_class=HTMLResponse)
def list_payees(
    request: Request,
    search: Optional[str] = None,
    payee_type: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Payees list page."""
    return banking_web_service.list_payees_response(request, auth, db, search, payee_type, page)


@router.get("/payees/new", response_class=HTMLResponse)
def new_payee_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New payee form page."""
    return banking_web_service.payee_new_form_response(request, auth, db)


@router.get("/payees/{payee_id}", response_class=HTMLResponse)
def view_payee(
    request: Request,
    payee_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Payee detail/edit page."""
    return banking_web_service.payee_detail_response(request, auth, db, payee_id)


@router.get("/rules", response_class=HTMLResponse)
def list_transaction_rules(
    request: Request,
    rule_type: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Transaction rules list page."""
    return banking_web_service.list_rules_response(request, auth, db, rule_type, page)


@router.get("/rules/new", response_class=HTMLResponse)
def new_rule_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New transaction rule form page."""
    return banking_web_service.rule_new_form_response(request, auth, db)


@router.get("/rules/{rule_id}", response_class=HTMLResponse)
def view_rule(
    request: Request,
    rule_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Transaction rule detail/edit page."""
    return banking_web_service.rule_detail_response(request, auth, db, rule_id)
