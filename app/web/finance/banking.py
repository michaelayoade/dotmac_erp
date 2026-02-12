"""
Banking Web Routes.

HTML template routes for Bank Accounts, Statements, and Reconciliations.
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.finance.banking.web import banking_web_service
from app.web.deps import WebAuthContext, get_db, require_finance_access

router = APIRouter(prefix="/banking", tags=["banking-web"])


@router.get("/accounts", response_class=HTMLResponse)
def list_bank_accounts(
    request: Request,
    search: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Bank accounts list page."""
    return banking_web_service.list_accounts_response(
        request, auth, db, search, status, page
    )


@router.get("/accounts/new", response_class=HTMLResponse)
def new_bank_account_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New bank account form page."""
    return banking_web_service.account_new_form_response(request, auth, db)


@router.post("/accounts/new")
async def new_bank_account_submit(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Create new bank account from form."""
    return await banking_web_service.create_account_response(request, auth, db)


@router.post("/accounts/{account_id}/edit")
async def edit_bank_account_submit(
    request: Request,
    account_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Update bank account from form."""
    return await banking_web_service.update_account_response(
        request, auth, db, account_id
    )


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
    account_id: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
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


@router.post("/statements/import", response_class=HTMLResponse)
async def import_statement_submit(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Handle bank statement import submission."""
    return await banking_web_service.statement_import_submit_response(request, auth, db)


@router.get("/statements/sample-csv")
def download_sample_csv(
    _auth: WebAuthContext = Depends(require_finance_access),
    format: str = Query(default="type"),
):
    """Download a sample CSV template for bank statement import."""
    from io import BytesIO

    from fastapi.responses import StreamingResponse

    from app.services.finance.banking import bank_statement_service

    content, filename = bank_statement_service.build_sample_csv(format)
    buf = BytesIO(content)

    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/statements/bulk-delete")
async def bulk_delete_statements(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Bulk delete statement batches."""
    return await banking_web_service.bulk_delete_statements_response(request, auth, db)


@router.post("/statements/bulk-export")
async def bulk_export_statements(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Export selected statements to CSV."""
    return await banking_web_service.bulk_export_statements_response(request, auth, db)


@router.get("/statements/{statement_id}", response_class=HTMLResponse)
def view_statement(
    request: Request,
    statement_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Statement detail page with lines."""
    return banking_web_service.statement_detail_response(
        request, auth, db, statement_id
    )


@router.post("/statements/{statement_id}/apply-rules")
async def apply_rules(
    request: Request,
    statement_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Apply categorization rules to statement lines."""
    await request.form()  # consume form body for CSRF validation
    return banking_web_service.apply_rules_response(request, auth, db, statement_id)


@router.post("/statements/{statement_id}/lines/{line_id}/accept")
async def accept_suggestion(
    request: Request,
    statement_id: str,
    line_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Accept a categorization suggestion for a statement line."""
    await request.form()  # consume form body for CSRF validation
    return banking_web_service.accept_suggestion_response(
        request, auth, db, statement_id, line_id
    )


@router.post("/statements/{statement_id}/lines/{line_id}/reject")
async def reject_suggestion(
    request: Request,
    statement_id: str,
    line_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Reject a categorization suggestion for a statement line."""
    await request.form()  # consume form body for CSRF validation
    return banking_web_service.reject_suggestion_response(
        request, auth, db, statement_id, line_id
    )


@router.post("/statements/{statement_id}/delete")
async def delete_statement(
    request: Request,
    statement_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Delete a bank statement batch and its lines."""
    await request.form()  # consume form body for CSRF validation
    return banking_web_service.delete_statement_response(
        request, auth, db, statement_id
    )


@router.post("/statements/{statement_id}/lines/{line_id}/match")
async def match_statement_line(
    request: Request,
    statement_id: str,
    line_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Accept a GL transaction match for a statement line (JSON from Alpine.js)."""
    return await banking_web_service.match_statement_line_response(
        request, auth, db, statement_id, line_id
    )


@router.post("/statements/{statement_id}/lines/{line_id}/unmatch")
async def unmatch_statement_line(
    request: Request,
    statement_id: str,
    line_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Remove a direct match from a statement line (JSON from Alpine.js)."""
    return await banking_web_service.unmatch_statement_line_response(
        request, auth, db, statement_id, line_id
    )


@router.get("/reconciliations", response_class=HTMLResponse)
def list_reconciliations(
    request: Request,
    account_id: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
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
    account_id: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New reconciliation form page."""
    return banking_web_service.reconciliation_new_form_response(
        request, auth, db, account_id=account_id
    )


@router.post("/reconciliations/new")
async def create_reconciliation_submit(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Create a new reconciliation from form submission."""
    from fastapi.responses import RedirectResponse

    return await banking_web_service.create_reconciliation_response(
        request, auth, db, RedirectResponse
    )


@router.post("/reconciliations/{reconciliation_id}/auto-match")
async def reconciliation_auto_match(
    request: Request,
    reconciliation_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Run auto-match on a reconciliation (HTMX action)."""
    return await banking_web_service.reconciliation_action_response(
        request, auth, db, reconciliation_id, "auto_match"
    )


@router.post("/reconciliations/{reconciliation_id}/submit")
async def reconciliation_submit_for_review(
    request: Request,
    reconciliation_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Submit reconciliation for review (HTMX action)."""
    return await banking_web_service.reconciliation_action_response(
        request, auth, db, reconciliation_id, "submit"
    )


@router.post("/reconciliations/{reconciliation_id}/approve")
async def reconciliation_approve(
    request: Request,
    reconciliation_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Approve a reconciliation (HTMX action)."""
    return await banking_web_service.reconciliation_action_response(
        request, auth, db, reconciliation_id, "approve"
    )


@router.post("/reconciliations/{reconciliation_id}/reject")
async def reconciliation_reject(
    request: Request,
    reconciliation_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Reject a reconciliation (HTMX action)."""
    return await banking_web_service.reconciliation_action_response(
        request, auth, db, reconciliation_id, "reject"
    )


@router.post("/reconciliations/{reconciliation_id}/matches")
async def reconciliation_add_match(
    request: Request,
    reconciliation_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Add a manual match (JSON from Alpine.js fetch)."""
    return await banking_web_service.reconciliation_match_response(
        request, auth, db, reconciliation_id
    )


@router.post("/reconciliations/{reconciliation_id}/multi-match")
async def reconciliation_multi_match(
    request: Request,
    reconciliation_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Add multi-match (JSON from Alpine.js fetch)."""
    return await banking_web_service.reconciliation_multi_match_response(
        request, auth, db, reconciliation_id
    )


@router.get("/reconciliations/{reconciliation_id}", response_class=HTMLResponse)
def view_reconciliation(
    request: Request,
    reconciliation_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Reconciliation workspace page."""
    return banking_web_service.reconciliation_detail_response(
        request, auth, db, reconciliation_id
    )


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
    search: str | None = None,
    payee_type: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Payees list page."""
    return banking_web_service.list_payees_response(
        request, auth, db, search, payee_type, page
    )


@router.get("/payees/new", response_class=HTMLResponse)
def new_payee_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New payee form page."""
    return banking_web_service.payee_new_form_response(request, auth, db)


@router.post("/payees/new")
async def new_payee_submit(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Create new payee from form."""
    return await banking_web_service.create_payee_response(request, auth, db)


@router.post("/payees/{payee_id}/edit")
async def edit_payee_submit(
    request: Request,
    payee_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Update payee from form."""
    return await banking_web_service.update_payee_response(request, auth, db, payee_id)


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
    rule_type: str | None = None,
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


@router.post("/rules/new")
async def create_rule(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Create a new transaction rule."""
    form = await request.form()
    form_data = dict(form)
    return banking_web_service.create_rule_response(request, auth, db, form_data)


@router.post("/rules/reorder")
async def reorder_rule(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Swap a rule's position up or down in evaluation order."""
    form = await request.form()
    rule_id = str(form.get("rule_id", ""))
    direction = str(form.get("direction", ""))
    if not rule_id or direction not in ("up", "down"):
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="rule_id and direction required")
    return banking_web_service.reorder_rules_response(
        request, auth, db, rule_id, direction
    )


@router.get("/rules/{rule_id}", response_class=HTMLResponse)
def view_rule(
    request: Request,
    rule_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Transaction rule detail/edit page."""
    return banking_web_service.rule_detail_response(request, auth, db, rule_id)


@router.post("/rules/{rule_id}/edit")
async def update_rule(
    request: Request,
    rule_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Update an existing transaction rule."""
    form = await request.form()
    form_data = dict(form)
    return banking_web_service.update_rule_response(
        request, auth, db, rule_id, form_data
    )
