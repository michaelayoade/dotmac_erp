"""
Sales Order Web Routes.

HTML template routes for sales order management.
"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.finance.ar.web import sales_order_web_service
from app.web.deps import WebAuthContext, get_db, require_finance_access

router = APIRouter(prefix="/sales-orders", tags=["sales-orders-web"])


# =============================================================================
# Sales Order List
# =============================================================================


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def sales_order_list(
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
    """Sales order list page."""
    return sales_order_web_service.list_response(
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
# New Sales Order
# =============================================================================


@router.get("/new", response_class=HTMLResponse)
def new_so_form(
    request: Request,
    customer_id: str | None = None,
    quote_id: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New sales order form."""
    return sales_order_web_service.new_form_response(
        request, auth, db, customer_id, quote_id
    )


@router.post("/new", response_class=HTMLResponse)
def create_sales_order(
    request: Request,
    customer_id: str = Form(...),
    order_date: str = Form(...),
    currency_code: str | None = Form(None),
    exchange_rate: str | None = Form(None),
    customer_po_number: str | None = Form(None),
    requested_date: str | None = Form(None),
    promised_date: str | None = Form(None),
    payment_terms_id: str | None = Form(None),
    ship_to_name: str | None = Form(None),
    ship_to_address: str | None = Form(None),
    ship_to_city: str | None = Form(None),
    ship_to_state: str | None = Form(None),
    ship_to_postal_code: str | None = Form(None),
    ship_to_country: str | None = Form(None),
    shipping_method: str | None = Form(None),
    allow_partial_shipment: str | None = Form(None),
    customer_notes: str | None = Form(None),
    internal_notes: str | None = Form(None),
    lines_json: str = Form("[]"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Create new sales order."""
    return sales_order_web_service.create_response(
        request,
        auth,
        db,
        customer_id=customer_id,
        order_date=order_date,
        lines_json=lines_json,
        currency_code=currency_code,
        exchange_rate=exchange_rate,
        customer_po_number=customer_po_number,
        requested_date=requested_date,
        promised_date=promised_date,
        payment_terms_id=payment_terms_id,
        ship_to_name=ship_to_name,
        ship_to_address=ship_to_address,
        ship_to_city=ship_to_city,
        ship_to_state=ship_to_state,
        ship_to_postal_code=ship_to_postal_code,
        ship_to_country=ship_to_country,
        shipping_method=shipping_method,
        allow_partial_shipment=allow_partial_shipment is not None,
        customer_notes=customer_notes,
        internal_notes=internal_notes,
    )


# =============================================================================
# Sales Order Detail
# =============================================================================


@router.get("/{so_id}", response_class=HTMLResponse)
def sales_order_detail(
    request: Request,
    so_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Sales order detail page."""
    return sales_order_web_service.detail_response(request, auth, db, so_id)


# =============================================================================
# Sales Order Actions
# =============================================================================


@router.post("/{so_id}/submit", response_class=HTMLResponse)
def submit_order(
    request: Request,
    so_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Submit sales order for approval."""
    return sales_order_web_service.submit_response(request, auth, db, so_id)


@router.post("/{so_id}/approve", response_class=HTMLResponse)
def approve_order(
    request: Request,
    so_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Approve sales order."""
    return sales_order_web_service.approve_response(request, auth, db, so_id)


@router.post("/{so_id}/confirm", response_class=HTMLResponse)
def confirm_order(
    request: Request,
    so_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Confirm sales order."""
    return sales_order_web_service.confirm_response(request, auth, db, so_id)


@router.post("/{so_id}/cancel", response_class=HTMLResponse)
def cancel_order(
    request: Request,
    so_id: str,
    reason: str | None = Form(None),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Cancel sales order."""
    return sales_order_web_service.cancel_response(request, auth, db, so_id, reason)


@router.post("/{so_id}/hold", response_class=HTMLResponse)
def hold_order(
    request: Request,
    so_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Put sales order on hold."""
    return sales_order_web_service.hold_response(request, auth, db, so_id)


@router.post("/{so_id}/release", response_class=HTMLResponse)
def release_order(
    request: Request,
    so_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Release sales order from hold."""
    return sales_order_web_service.release_response(request, auth, db, so_id)


@router.post("/{so_id}/create-invoice", response_class=HTMLResponse)
def create_invoice_from_order(
    request: Request,
    so_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Create invoice from shipped lines."""
    return sales_order_web_service.create_invoice_response(request, auth, db, so_id)


# =============================================================================
# Shipment Actions
# =============================================================================


@router.get("/{so_id}/ship", response_class=HTMLResponse)
def ship_order_form(
    request: Request,
    so_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Shipment form for sales order."""
    return sales_order_web_service.shipment_form_response(request, auth, db, so_id)


@router.post("/{so_id}/ship", response_class=HTMLResponse)
def create_shipment(
    request: Request,
    so_id: str,
    shipment_date: str = Form(...),
    carrier: str | None = Form(None),
    tracking_number: str | None = Form(None),
    shipping_method: str | None = Form(None),
    notes: str | None = Form(None),
    line_quantities_json: str = Form("[]"),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Create shipment for sales order."""
    return sales_order_web_service.create_shipment_response(
        request,
        auth,
        db,
        so_id=so_id,
        shipment_date=shipment_date,
        line_quantities_json=line_quantities_json,
        carrier=carrier,
        tracking_number=tracking_number,
        shipping_method=shipping_method,
        notes=notes,
    )


@router.post("/shipments/{shipment_id}/deliver", response_class=HTMLResponse)
def mark_delivered(
    request: Request,
    shipment_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Mark shipment as delivered."""
    return sales_order_web_service.mark_delivered_response(
        request, auth, db, shipment_id
    )
