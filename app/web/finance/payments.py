"""
Payment Web Routes.

HTML pages for payment flow.
"""

import logging
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.common import coerce_uuid
from app.services.finance.payments import PaymentService
from app.services.finance.payments.web import payment_web_service
from app.services.finance.platform.authorization import AuthorizationService
from app.templates import templates
from app.web.deps import (
    WebAuthContext,
    base_context,
    get_db,
    get_db_for_org,
    optional_web_auth,
    require_finance_access,
    require_web_auth,
)

router = APIRouter(prefix="/payments", tags=["payments-web"])
logger = logging.getLogger(__name__)


def _require_expense_reimburse(
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
) -> WebAuthContext:
    if auth.is_admin:
        return auth
    if auth.has_permission("expense:claims:reimburse"):
        return auth
    if auth.person_id and auth.organization_id:
        if AuthorizationService.check_permission(
            db,
            coerce_uuid(auth.person_id),
            "expense:claims:reimburse",
            coerce_uuid(auth.organization_id),
        ):
            return auth
    raise HTTPException(
        status_code=403, detail="Permission 'expense:claims:reimburse' required"
    )


@router.get("/callback", response_class=HTMLResponse)
def payment_callback(
    request: Request,
    reference: str = Query(...),
    trxref: str | None = Query(None),  # Paystack also sends this
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """
    Payment callback page.

    Customer is redirected here after Paystack checkout.
    This page shows the payment status to the customer.
    """
    context = payment_web_service.payment_callback_context(db, reference, trxref)
    context["is_authenticated"] = auth.is_authenticated
    return templates.TemplateResponse(
        request, "finance/payments/callback.html", context
    )


@router.get("/pay/{invoice_id}", response_class=HTMLResponse)
def pay_invoice_page(
    request: Request,
    invoice_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """
    Pay invoice page with Paystack button.

    Shows invoice details and a button to initiate payment.
    """
    result = payment_web_service.pay_invoice_context(
        db, auth.organization_id, invoice_id
    )
    redirect_url = result.get("redirect_url")
    if redirect_url:
        return RedirectResponse(redirect_url, status_code=302)

    page_title = result.get("context", {}).get("page_title", "Pay Invoice")
    context = base_context(request, auth, page_title, "ar", db=db)
    context.update(result.get("context", {}))

    return templates.TemplateResponse(
        request,
        "finance/payments/pay_invoice.html",
        context,
    )


@router.get("/reimburse/{expense_claim_id}", response_class=HTMLResponse)
def reimburse_expense_page(
    request: Request,
    expense_claim_id: str,
    auth: WebAuthContext = Depends(_require_expense_reimburse),
    db: Session = Depends(get_db_for_org),
):
    """
    Expense reimbursement page.

    Shows expense claim details and allows initiating a Paystack transfer.
    """
    result = payment_web_service.reimburse_expense_context(
        db, auth.organization_id, expense_claim_id
    )
    redirect_url = result.get("redirect_url")
    if redirect_url:
        return RedirectResponse(redirect_url, status_code=302)

    page_title = result.get("context", {}).get("page_title", "Reimburse Expense")
    context = base_context(request, auth, page_title, "expense", db=db)
    context.update(result.get("context", {}))
    context["error"] = request.query_params.get("error")
    context["success"] = request.query_params.get("success")

    return templates.TemplateResponse(
        request,
        "finance/payments/reimburse_expense.html",
        context,
    )


@router.post("/reimburse/{expense_claim_id}/reset-intent")
def reimburse_expense_reset_intent(
    expense_claim_id: str,
    reason: str | None = Form(None),
    force: bool = Form(False),
    auth: WebAuthContext = Depends(_require_expense_reimburse),
    db: Session = Depends(get_db_for_org),
):
    """
    Reset the latest failed/abandoned/expired reimbursement intent for retry.
    """
    claim_id = coerce_uuid(expense_claim_id)
    svc = PaymentService(db, coerce_uuid(auth.organization_id))
    try:
        svc.reset_expense_payment_intent(
            expense_claim_id=claim_id,
            reason=(reason or None),
            force=force,
        )
    except HTTPException as exc:
        return RedirectResponse(
            f"/finance/payments/reimburse/{expense_claim_id}?error={quote_plus(str(exc.detail))}",
            status_code=303,
        )
    except Exception:
        logger.exception(
            "Failed to reset expense reimbursement intent %s", expense_claim_id
        )
        return RedirectResponse(
            f"/finance/payments/reimburse/{expense_claim_id}?error=Failed+to+reset+payment+intent",
            status_code=303,
        )

    return RedirectResponse(
        f"/finance/payments/reimburse/{expense_claim_id}?success=Payment+intent+reset+for+retry",
        status_code=303,
    )


@router.get("/transfers", response_class=HTMLResponse)
def transfer_list(
    request: Request,
    search: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=25, ge=1, le=200),
    auth: WebAuthContext = Depends(_require_expense_reimburse),
    db: Session = Depends(get_db_for_org),
):
    """
    Transfer management page.

    Lists all outbound transfers (expense reimbursements).
    """
    context = base_context(request, auth, "Transfers", "expense", db=db)
    context.update(
        payment_web_service.transfer_list_context(
            db, auth.organization_id, search, status, page, per_page=limit
        )
    )

    return templates.TemplateResponse(
        request,
        "finance/payments/transfers.html",
        context,
    )


@router.get("/history", response_class=HTMLResponse)
def payment_history(
    request: Request,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(_require_expense_reimburse),
    db: Session = Depends(get_db_for_org),
):
    """
    Payment history page.

    Lists all payment intents for the organization.
    """
    context = base_context(request, auth, "Payment History", "ar", db=db)
    context.update(
        payment_web_service.payment_history_context(
            db, auth.organization_id, status, page
        )
    )

    return templates.TemplateResponse(
        request,
        "finance/payments/history.html",
        context,
    )
