"""
Payment Web Routes.

HTML pages for payment flow.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.templates import templates
from app.web.deps import (
    get_db,
    require_finance_access,
    optional_web_auth,
    WebAuthContext,
    base_context,
)
from app.models.domain_settings import SettingDomain
from app.services.settings_spec import resolve_value

router = APIRouter(prefix="/payments", tags=["payments-web"])


@router.get("/callback", response_class=HTMLResponse)
def payment_callback(
    request: Request,
    reference: str = Query(...),
    trxref: Optional[str] = Query(None),  # Paystack also sends this
    db: Session = Depends(get_db),
    auth: WebAuthContext = Depends(optional_web_auth),
):
    """
    Payment callback page.

    Customer is redirected here after Paystack checkout.
    This page shows the payment status to the customer.
    """
    from app.models.finance.payments import PaymentIntent, PaymentIntentStatus

    # Use trxref if reference is empty (Paystack sends both)
    ref = reference or trxref or ""

    intent = (
        db.query(PaymentIntent)
        .filter(PaymentIntent.paystack_reference == ref)
        .first()
    )

    status = "unknown"
    message = "Payment status unknown. Please contact support."
    invoice_number = None
    customer_payment_id = None
    amount = None
    currency = None

    if intent:
        amount = float(intent.amount)
        currency = intent.currency_code
        invoice_number = intent.intent_metadata.get("invoice_number") if intent.intent_metadata else None
        customer_payment_id = intent.customer_payment_id

        if intent.status == PaymentIntentStatus.COMPLETED:
            status = "success"
            message = "Payment successful! Your invoice has been paid."
        elif intent.status == PaymentIntentStatus.FAILED:
            status = "failed"
            error = (
                intent.gateway_response.get("error", "Payment failed")
                if intent.gateway_response
                else "Payment failed"
            )
            message = f"Payment failed: {error}. Please try again."
        elif intent.status == PaymentIntentStatus.ABANDONED:
            status = "abandoned"
            message = "Payment was cancelled. Please try again if you wish to complete the payment."
        elif intent.status in [PaymentIntentStatus.PENDING, PaymentIntentStatus.PROCESSING]:
            status = "pending"
            message = "Payment is being processed. You will receive confirmation shortly."
        elif intent.status == PaymentIntentStatus.EXPIRED:
            status = "expired"
            message = "Payment session expired. Please initiate a new payment."

    return templates.TemplateResponse(
        request,
        "finance/payments/callback.html",
        {
            "title": f"Payment {status.title()}",
            "status": status,
            "message": message,
            "reference": ref,
            "invoice_number": invoice_number,
            "amount": amount,
            "currency": currency,
            "customer_payment_id": str(customer_payment_id) if customer_payment_id else None,
            "is_authenticated": auth.is_authenticated,
        },
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
    from app.models.finance.ar.invoice import Invoice, InvoiceStatus
    from app.models.finance.ar.customer import Customer

    invoice = db.get(Invoice, invoice_id)
    if not invoice or invoice.organization_id != auth.organization_id:
        return RedirectResponse("/finance/ar/invoices", status_code=302)

    # Check if invoice is payable
    payable_statuses = [
        InvoiceStatus.POSTED,
        InvoiceStatus.PARTIALLY_PAID,
        InvoiceStatus.OVERDUE,
    ]
    is_payable = invoice.status in payable_statuses and invoice.balance_due > 0

    customer = db.get(Customer, invoice.customer_id) if invoice.customer_id else None

    # Check Paystack configuration
    paystack_enabled = resolve_value(db, SettingDomain.payments, "paystack_enabled")
    paystack_public_key = resolve_value(db, SettingDomain.payments, "paystack_public_key")

    context = base_context(request, auth, f"Pay Invoice {invoice.invoice_number}", "ar", db=db)
    context.update({
        "invoice": invoice,
        "customer": customer,
        "is_payable": is_payable,
        "paystack_enabled": bool(paystack_enabled),
        "paystack_public_key": str(paystack_public_key) if paystack_public_key else None,
        "has_email": bool(customer and customer.email) if customer else False,
    })

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
    from app.models.expense.expense_claim import ExpenseClaim, ExpenseClaimStatus
    from app.models.people.employee import Employee

    expense_claim = db.get(ExpenseClaim, expense_claim_id)
    if not expense_claim or expense_claim.organization_id != auth.organization_id:
        return RedirectResponse("/expense/claims", status_code=302)

    # Check if claim can be reimbursed
    can_reimburse = expense_claim.status == ExpenseClaimStatus.APPROVED

    # Get employee info
    employee = db.get(Employee, expense_claim.employee_id) if expense_claim.employee_id else None

    # Check Paystack configuration
    paystack_enabled = resolve_value(db, SettingDomain.payments, "paystack_enabled")
    transfers_enabled = resolve_value(db, SettingDomain.payments, "paystack_transfers_enabled")

    context = base_context(request, auth, f"Reimburse {expense_claim.claim_number}", "expense", db=db)
    context.update({
        "expense_claim": expense_claim,
        "employee": employee,
        "can_reimburse": can_reimburse,
        "paystack_enabled": bool(paystack_enabled),
        "transfers_enabled": bool(transfers_enabled),
    })

    return templates.TemplateResponse(
        request,
        "finance/payments/reimburse_expense.html",
        context,
    )


@router.get("/transfers", response_class=HTMLResponse)
def transfer_list(
    request: Request,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """
    Transfer management page.

    Lists all outbound transfers (expense reimbursements).
    """
    from app.models.finance.payments import PaymentIntent, PaymentIntentStatus, PaymentDirection

    per_page = 20
    offset = (page - 1) * per_page

    query = (
        db.query(PaymentIntent)
        .filter(
            PaymentIntent.organization_id == auth.organization_id,
            PaymentIntent.direction == PaymentDirection.OUTBOUND,
        )
        .order_by(PaymentIntent.created_at.desc())
    )

    if status:
        try:
            status_enum = PaymentIntentStatus(status.upper())
            query = query.filter(PaymentIntent.status == status_enum)
        except ValueError:
            pass

    total = query.count()
    intents = query.limit(per_page).offset(offset).all()

    context = base_context(request, auth, "Transfers", "expense", db=db)
    context.update({
        "intents": intents,
        "current_page": page,
        "total_pages": (total + per_page - 1) // per_page,
        "total": total,
        "status_filter": status,
        "statuses": [s.value for s in PaymentIntentStatus],
    })

    return templates.TemplateResponse(
        request,
        "finance/payments/transfers.html",
        context,
    )


@router.get("/history", response_class=HTMLResponse)
def payment_history(
    request: Request,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """
    Payment history page.

    Lists all payment intents for the organization.
    """
    from app.models.finance.payments import PaymentIntent, PaymentIntentStatus

    per_page = 20
    offset = (page - 1) * per_page

    query = (
        db.query(PaymentIntent)
        .filter(PaymentIntent.organization_id == auth.organization_id)
        .order_by(PaymentIntent.created_at.desc())
    )

    if status:
        try:
            status_enum = PaymentIntentStatus(status.upper())
            query = query.filter(PaymentIntent.status == status_enum)
        except ValueError:
            pass

    total = query.count()
    intents = query.limit(per_page).offset(offset).all()

    context = base_context(request, auth, "Payment History", "ar", db=db)
    context.update({
        "intents": intents,
        "current_page": page,
        "total_pages": (total + per_page - 1) // per_page,
        "total": total,
        "status_filter": status,
        "statuses": [s.value for s in PaymentIntentStatus],
    })

    return templates.TemplateResponse(
        request,
        "finance/payments/history.html",
        context,
    )
