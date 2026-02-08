"""
Payment Web Routes.

HTML pages for payment flow.
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.finance.payments.web import payment_web_service
from app.templates import templates
from app.web.deps import (
    WebAuthContext,
    base_context,
    get_db,
    optional_web_auth,
    require_finance_access,
)

router = APIRouter(prefix="/payments", tags=["payments-web"])


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
    db: Session = Depends(get_db),
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
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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

    return templates.TemplateResponse(
        request,
        "finance/payments/reimburse_expense.html",
        context,
    )


@router.get("/transfers", response_class=HTMLResponse)
def transfer_list(
    request: Request,
    search: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """
    Transfer management page.

    Lists all outbound transfers (expense reimbursements).
    """
    context = base_context(request, auth, "Transfers", "expense", db=db)
    context.update(
        payment_web_service.transfer_list_context(
            db, auth.organization_id, search, status, page
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
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
