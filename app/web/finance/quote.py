"""
Quote Web Routes.

HTML template routes for sales quote management.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.finance.ar.web import quote_web_service
from app.web.deps import WebAuthContext, get_db, require_finance_access

router = APIRouter(prefix="/quotes", tags=["quotes-web"])


# =============================================================================
# Quote List
# =============================================================================


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def quote_list(
    request: Request,
    status: str | None = None,
    customer_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    sort: str | None = None,
    sort_dir: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Quote list page."""
    return quote_web_service.list_response(
        request,
        auth,
        db,
        status,
        customer_id,
        start_date,
        end_date,
        sort,
        sort_dir,
    )


# =============================================================================
# New Quote
# =============================================================================


@router.get("/new", response_class=HTMLResponse)
def new_quote_form(
    request: Request,
    customer_id: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New quote form."""
    return quote_web_service.new_form_response(request, auth, db, customer_id)


@router.post("/new", response_class=HTMLResponse)
def create_quote(
    request: Request,
    customer_id: str = Form(...),
    quote_date: str = Form(...),
    valid_until: str = Form(...),
    currency_code: str | None = Form(None),
    exchange_rate: str | None = Form(None),
    contact_name: str | None = Form(None),
    contact_email: str | None = Form(None),
    payment_terms_id: str | None = Form(None),
    customer_notes: str | None = Form(None),
    internal_notes: str | None = Form(None),
    terms_and_conditions: str | None = Form(None),
    lines_json: str = Form("[]"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Create new quote."""
    return quote_web_service.create_response(
        request,
        auth,
        db,
        customer_id=customer_id,
        quote_date=quote_date,
        valid_until=valid_until,
        lines_json=lines_json,
        currency_code=currency_code,
        exchange_rate=exchange_rate,
        contact_name=contact_name,
        contact_email=contact_email,
        payment_terms_id=payment_terms_id,
        customer_notes=customer_notes,
        internal_notes=internal_notes,
        terms_and_conditions=terms_and_conditions,
    )


# =============================================================================
# Quote Detail
# =============================================================================


@router.get("/{quote_id}", response_class=HTMLResponse)
def quote_detail(
    request: Request,
    quote_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Quote detail page."""
    return quote_web_service.detail_response(request, auth, db, quote_id)


# =============================================================================
# Quote Actions
# =============================================================================


@router.post("/{quote_id}/send", response_class=HTMLResponse)
def send_quote(
    request: Request,
    quote_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Send quote."""
    return quote_web_service.send_response(request, auth, db, quote_id)


@router.post("/{quote_id}/accept", response_class=HTMLResponse)
def accept_quote(
    request: Request,
    quote_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Accept quote."""
    return quote_web_service.accept_response(request, auth, db, quote_id)


@router.post("/{quote_id}/reject", response_class=HTMLResponse)
def reject_quote(
    request: Request,
    quote_id: str,
    reason: str | None = Form(None),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Reject quote."""
    return quote_web_service.reject_response(request, auth, db, quote_id, reason)


@router.post("/{quote_id}/convert-to-invoice", response_class=HTMLResponse)
def convert_to_invoice(
    request: Request,
    quote_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Convert quote to invoice."""
    return quote_web_service.convert_to_invoice_response(request, auth, db, quote_id)


@router.post("/{quote_id}/convert-to-so", response_class=HTMLResponse)
def convert_to_sales_order(
    request: Request,
    quote_id: str,
    customer_po_number: str | None = Form(None),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Convert quote to sales order."""
    return quote_web_service.convert_to_sales_order_response(
        request, auth, db, quote_id, customer_po_number
    )


@router.post("/{quote_id}/void", response_class=HTMLResponse)
def void_quote(
    request: Request,
    quote_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Void quote."""
    return quote_web_service.void_response(request, auth, db, quote_id)
