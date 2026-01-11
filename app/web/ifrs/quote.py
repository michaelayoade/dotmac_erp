"""
Quote Web Routes.

HTML template routes for sales quote management.
"""
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.ifrs.ar.customer import Customer
from app.models.ifrs.ar.payment_terms import PaymentTerms
from app.models.ifrs.ar.quote import Quote, QuoteStatus
from app.models.ifrs.gl.account import Account
from app.models.ifrs.gl.account_category import AccountCategory, IFRSCategory
from app.models.ifrs.tax.tax_code import TaxCode
from app.services.common import coerce_uuid
from app.config import settings
from app.services.ifrs.ar.quote import quote_service
from app.services.ifrs.platform.org_context import org_context_service
from app.services.ifrs.platform.currency_context import get_currency_context
from app.templates import templates
from app.web.deps import get_db, require_web_auth, WebAuthContext, base_context


router = APIRouter(prefix="/quotes", tags=["quotes-web"])


def _format_currency(
    amount,
    currency: str = settings.default_presentation_currency_code,
):
    if amount is None:
        return f"{currency} 0.00"
    value = Decimal(str(amount))
    return f"{currency} {value:,.2f}"


def _format_date(value):
    return value.strftime("%Y-%m-%d") if value else ""


def _get_form_context(db: Session, org_id) -> dict:
    """Get common form context for quotes."""
    # Customers
    customers = (
        db.query(Customer)
        .filter(Customer.organization_id == org_id, Customer.is_active.is_(True))
        .order_by(Customer.customer_name)
        .all()
    )
    customer_options = [
        {"customer_id": str(c.customer_id), "name": c.customer_name, "email": c.email}
        for c in customers
    ]

    # Revenue accounts
    revenue_accounts = (
        db.query(Account)
        .join(AccountCategory)
        .filter(
            Account.organization_id == org_id,
            Account.is_active.is_(True),
            AccountCategory.ifrs_category == IFRSCategory.REVENUE,
        )
        .order_by(Account.account_code)
        .all()
    )
    revenue_options = [
        {"account_id": str(a.account_id), "code": a.account_code, "name": a.account_name}
        for a in revenue_accounts
    ]

    # Tax codes
    tax_codes = (
        db.query(TaxCode)
        .filter(TaxCode.organization_id == org_id, TaxCode.is_active.is_(True))
        .order_by(TaxCode.tax_code)
        .all()
    )
    tax_options = [
        {"tax_code_id": str(t.tax_code_id), "code": t.tax_code, "rate": float(t.rate)}
        for t in tax_codes
    ]

    # Payment terms
    payment_terms = (
        db.query(PaymentTerms)
        .filter(PaymentTerms.organization_id == org_id, PaymentTerms.is_active.is_(True))
        .order_by(PaymentTerms.term_name)
        .all()
    )
    terms_options = [
        {"terms_id": str(t.payment_terms_id), "name": t.term_name}
        for t in payment_terms
    ]

    context = {
        "customers": customer_options,
        "revenue_accounts": revenue_options,
        "tax_codes": tax_options,
        "payment_terms": terms_options,
        "today": _format_date(date.today()),
        "default_valid_until": _format_date(date.today() + timedelta(days=30)),
    }
    context.update(get_currency_context(db, str(org_id)))
    return context


# =============================================================================
# Quote List
# =============================================================================

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def quote_list(
    request: Request,
    status: Optional[str] = None,
    customer_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Quote list page."""
    org_id = coerce_uuid(str(auth.organization_id))

    # Get quotes
    status_filter = QuoteStatus(status) if status else None
    quotes = quote_service.list_quotes(
        db,
        str(auth.organization_id),
        customer_id=customer_id,
        status=status_filter,
        start_date=start_date,
        end_date=end_date,
    )

    # Format for template
    items = []
    for q in quotes:
        items.append({
            "quote_id": str(q.quote_id),
            "quote_number": q.quote_number,
            "quote_date": _format_date(q.quote_date),
            "valid_until": _format_date(q.valid_until),
            "customer_name": q.customer.customer_name if q.customer else "-",
            "total_amount": _format_currency(q.total_amount, q.currency_code),
            "status": q.status.value,
            "is_expired": q.is_expired,
        })

    # Status counts
    status_counts = {}
    for s in QuoteStatus:
        count = db.query(Quote).filter(
            Quote.organization_id == org_id,
            Quote.status == s,
        ).count()
        status_counts[s.value] = count

    # Customers for filter
    customers = (
        db.query(Customer)
        .filter(Customer.organization_id == org_id)
        .order_by(Customer.customer_name)
        .all()
    )

    context = base_context(request, auth, "Quotes", "quotes")
    context.update({
        "quotes": items,
        "filter_status": status,
        "filter_customer_id": customer_id,
        "filter_start_date": start_date,
        "filter_end_date": end_date,
        "status_counts": status_counts,
        "statuses": [s.value for s in QuoteStatus],
        "customers": [{"id": str(c.customer_id), "name": c.customer_name} for c in customers],
    })
    return templates.TemplateResponse(request, "ifrs/ar/quotes.html", context)


# =============================================================================
# New Quote
# =============================================================================

@router.get("/new", response_class=HTMLResponse)
def new_quote_form(
    request: Request,
    customer_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New quote form."""
    org_id = coerce_uuid(str(auth.organization_id))

    context = base_context(request, auth, "New Quote", "quotes")
    context.update(_get_form_context(db, org_id))
    context["quote"] = None
    context["selected_customer_id"] = customer_id

    return templates.TemplateResponse(request, "ifrs/ar/quote_form.html", context)


@router.post("/new", response_class=HTMLResponse)
def create_quote(
    request: Request,
    customer_id: str = Form(...),
    quote_date: str = Form(...),
    valid_until: str = Form(...),
    currency_code: Optional[str] = Form(None),
    contact_name: Optional[str] = Form(None),
    contact_email: Optional[str] = Form(None),
    payment_terms_id: Optional[str] = Form(None),
    customer_notes: Optional[str] = Form(None),
    internal_notes: Optional[str] = Form(None),
    terms_and_conditions: Optional[str] = Form(None),
    # Lines (JSON string)
    lines_json: str = Form("[]"),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Create new quote."""
    import json

    try:
        lines = json.loads(lines_json) if lines_json else []

        if not currency_code:
            currency_code = org_context_service.get_functional_currency(
                db,
                auth.organization_id,
            )

        quote = quote_service.create(
            db,
            organization_id=str(auth.organization_id),
            customer_id=customer_id,
            quote_date=datetime.strptime(quote_date, "%Y-%m-%d").date(),
            valid_until=datetime.strptime(valid_until, "%Y-%m-%d").date(),
            created_by=str(auth.user_id),
            currency_code=currency_code,
            contact_name=contact_name,
            contact_email=contact_email,
            payment_terms_id=payment_terms_id if payment_terms_id else None,
            customer_notes=customer_notes,
            internal_notes=internal_notes,
            terms_and_conditions=terms_and_conditions,
            lines=lines,
        )
        db.commit()
        return RedirectResponse(f"/quotes/{quote.quote_id}", status_code=303)
    except Exception as e:
        db.rollback()
        org_id = coerce_uuid(str(auth.organization_id))
        context = base_context(request, auth, "New Quote", "quotes")
        context.update(_get_form_context(db, org_id))
        context["quote"] = None
        context["error"] = str(e)
        return templates.TemplateResponse(request, "ifrs/ar/quote_form.html", context)


# =============================================================================
# Quote Detail
# =============================================================================

@router.get("/{quote_id}", response_class=HTMLResponse)
def quote_detail(
    request: Request,
    quote_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Quote detail page."""
    org_id = coerce_uuid(str(auth.organization_id))
    quote = db.get(Quote, coerce_uuid(quote_id))

    if not quote or quote.organization_id != org_id:
        context = base_context(request, auth, "Quote Not Found", "quotes")
        context["quote"] = None
        return templates.TemplateResponse(request, "ifrs/ar/quote_detail.html", context)

    # Format quote data
    quote_data = {
        "quote_id": str(quote.quote_id),
        "quote_number": quote.quote_number,
        "quote_date": _format_date(quote.quote_date),
        "valid_until": _format_date(quote.valid_until),
        "customer_name": quote.customer.customer_name if quote.customer else "-",
        "customer_email": quote.customer.email if quote.customer else "-",
        "contact_name": quote.contact_name or "-",
        "contact_email": quote.contact_email or "-",
        "subtotal": _format_currency(quote.subtotal, quote.currency_code),
        "discount_amount": _format_currency(quote.discount_amount, quote.currency_code),
        "tax_amount": _format_currency(quote.tax_amount, quote.currency_code),
        "total_amount": _format_currency(quote.total_amount, quote.currency_code),
        "currency_code": quote.currency_code,
        "status": quote.status.value,
        "is_expired": quote.is_expired,
        "customer_notes": quote.customer_notes or "",
        "internal_notes": quote.internal_notes or "",
        "terms_and_conditions": quote.terms_and_conditions or "",
        "reference": quote.reference or "-",
        "payment_terms": quote.payment_terms.term_name if quote.payment_terms else "-",
        "sent_at": quote.sent_at.strftime("%Y-%m-%d %H:%M") if quote.sent_at else None,
        "viewed_at": quote.viewed_at.strftime("%Y-%m-%d %H:%M") if quote.viewed_at else None,
        "accepted_at": quote.accepted_at.strftime("%Y-%m-%d %H:%M") if quote.accepted_at else None,
        "rejected_at": quote.rejected_at.strftime("%Y-%m-%d %H:%M") if quote.rejected_at else None,
        "rejection_reason": quote.rejection_reason,
        "converted_at": quote.converted_at.strftime("%Y-%m-%d %H:%M") if quote.converted_at else None,
        "converted_to_invoice_id": str(quote.converted_to_invoice_id) if quote.converted_to_invoice_id else None,
        "converted_to_so_id": str(quote.converted_to_so_id) if quote.converted_to_so_id else None,
        "created_at": quote.created_at.strftime("%Y-%m-%d %H:%M") if quote.created_at else "",
        # Actions
        "can_edit": quote.status == QuoteStatus.DRAFT,
        "can_send": quote.status == QuoteStatus.DRAFT,
        "can_accept": quote.status in [QuoteStatus.SENT, QuoteStatus.VIEWED] and not quote.is_expired,
        "can_reject": quote.status in [QuoteStatus.SENT, QuoteStatus.VIEWED],
        "can_convert": quote.status == QuoteStatus.ACCEPTED,
        "can_void": quote.status not in [QuoteStatus.CONVERTED, QuoteStatus.VOID],
    }

    # Lines
    lines = []
    for line in quote.lines:
        lines.append({
            "line_number": line.line_number,
            "item_code": line.item_code or "-",
            "description": line.description,
            "quantity": str(line.quantity),
            "unit_price": _format_currency(line.unit_price, quote.currency_code),
            "discount": _format_currency(line.discount_amount, quote.currency_code),
            "tax": _format_currency(line.tax_amount, quote.currency_code),
            "line_total": _format_currency(line.line_total, quote.currency_code),
        })

    context = base_context(request, auth, f"Quote {quote.quote_number}", "quotes")
    context["quote"] = quote_data
    context["lines"] = lines
    return templates.TemplateResponse(request, "ifrs/ar/quote_detail.html", context)


# =============================================================================
# Quote Actions
# =============================================================================

@router.post("/{quote_id}/send", response_class=HTMLResponse)
def send_quote(
    request: Request,
    quote_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Send quote."""
    try:
        quote_service.send(db, quote_id, str(auth.user_id))
        db.commit()
    except Exception:
        db.rollback()
    return RedirectResponse(f"/quotes/{quote_id}", status_code=303)


@router.post("/{quote_id}/accept", response_class=HTMLResponse)
def accept_quote(
    request: Request,
    quote_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Accept quote."""
    try:
        quote_service.accept(db, quote_id)
        db.commit()
    except Exception:
        db.rollback()
    return RedirectResponse(f"/quotes/{quote_id}", status_code=303)


@router.post("/{quote_id}/reject", response_class=HTMLResponse)
def reject_quote(
    request: Request,
    quote_id: str,
    reason: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Reject quote."""
    try:
        quote_service.reject(db, quote_id, reason)
        db.commit()
    except Exception:
        db.rollback()
    return RedirectResponse(f"/quotes/{quote_id}", status_code=303)


@router.post("/{quote_id}/convert-to-invoice", response_class=HTMLResponse)
def convert_to_invoice(
    request: Request,
    quote_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Convert quote to invoice."""
    try:
        invoice = quote_service.convert_to_invoice(db, quote_id, str(auth.user_id))
        db.commit()
        return RedirectResponse(f"/ar/invoices/{invoice.invoice_id}", status_code=303)
    except Exception:
        db.rollback()
        return RedirectResponse(f"/quotes/{quote_id}", status_code=303)


@router.post("/{quote_id}/convert-to-so", response_class=HTMLResponse)
def convert_to_sales_order(
    request: Request,
    quote_id: str,
    customer_po_number: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Convert quote to sales order."""
    try:
        so = quote_service.convert_to_sales_order(
            db, quote_id, str(auth.user_id),
            customer_po_number=customer_po_number,
        )
        db.commit()
        return RedirectResponse(f"/sales-orders/{so.so_id}", status_code=303)
    except Exception:
        db.rollback()
        return RedirectResponse(f"/quotes/{quote_id}", status_code=303)


@router.post("/{quote_id}/void", response_class=HTMLResponse)
def void_quote(
    request: Request,
    quote_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Void quote."""
    try:
        quote_service.void(db, quote_id, str(auth.user_id))
        db.commit()
    except Exception:
        db.rollback()
    return RedirectResponse(f"/quotes/{quote_id}", status_code=303)
