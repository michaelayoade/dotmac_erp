"""
Sales Order Web Routes.

HTML template routes for sales order management.
"""
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.ifrs.ar.customer import Customer
from app.models.ifrs.ar.payment_terms import PaymentTerms
from app.models.ifrs.ar.sales_order import SalesOrder, SOStatus, FulfillmentStatus, Shipment
from app.models.ifrs.gl.account import Account
from app.models.ifrs.gl.account_category import AccountCategory, IFRSCategory
from app.models.ifrs.tax.tax_code import TaxCode
from app.services.common import coerce_uuid
from app.config import settings
from app.services.ifrs.ar.sales_order import sales_order_service
from app.services.ifrs.platform.org_context import org_context_service
from app.services.ifrs.platform.currency_context import get_currency_context
from app.templates import templates
from app.web.deps import get_db, require_web_auth, WebAuthContext, base_context


router = APIRouter(prefix="/sales-orders", tags=["sales-orders-web"])


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
    """Get common form context for sales orders."""
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
    }
    context.update(get_currency_context(db, str(org_id)))
    return context


# =============================================================================
# Sales Order List
# =============================================================================

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def sales_order_list(
    request: Request,
    status: Optional[str] = None,
    customer_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Sales order list page."""
    org_id = coerce_uuid(str(auth.organization_id))

    # Get sales orders
    status_filter = SOStatus(status) if status else None
    orders = sales_order_service.list_orders(
        db,
        str(auth.organization_id),
        customer_id=customer_id,
        status=status_filter,
        start_date=start_date,
        end_date=end_date,
    )

    # Format for template
    items = []
    for so in orders:
        items.append({
            "so_id": str(so.so_id),
            "so_number": so.so_number,
            "order_date": _format_date(so.order_date),
            "customer_name": so.customer.customer_name if so.customer else "-",
            "customer_po": so.customer_po_number or "-",
            "total_amount": _format_currency(so.total_amount, so.currency_code),
            "status": so.status.value,
            "is_fully_shipped": so.is_fully_shipped,
            "is_fully_invoiced": so.is_fully_invoiced,
        })

    # Status counts
    status_counts = {}
    for s in SOStatus:
        count = db.query(SalesOrder).filter(
            SalesOrder.organization_id == org_id,
            SalesOrder.status == s,
        ).count()
        status_counts[s.value] = count

    # Customers for filter
    customers = (
        db.query(Customer)
        .filter(Customer.organization_id == org_id)
        .order_by(Customer.customer_name)
        .all()
    )

    context = base_context(request, auth, "Sales Orders", "sales-orders")
    context.update({
        "orders": items,
        "filter_status": status,
        "filter_customer_id": customer_id,
        "filter_start_date": start_date,
        "filter_end_date": end_date,
        "status_counts": status_counts,
        "statuses": [s.value for s in SOStatus],
        "customers": [{"id": str(c.customer_id), "name": c.customer_name} for c in customers],
    })
    return templates.TemplateResponse(request, "ifrs/ar/sales_orders.html", context)


# =============================================================================
# New Sales Order
# =============================================================================

@router.get("/new", response_class=HTMLResponse)
def new_so_form(
    request: Request,
    customer_id: Optional[str] = None,
    quote_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New sales order form."""
    org_id = coerce_uuid(str(auth.organization_id))

    context = base_context(request, auth, "New Sales Order", "sales-orders")
    context.update(_get_form_context(db, org_id))
    context["order"] = None
    context["selected_customer_id"] = customer_id
    context["quote_id"] = quote_id

    return templates.TemplateResponse(request, "ifrs/ar/sales_order_form.html", context)


@router.post("/new", response_class=HTMLResponse)
def create_sales_order(
    request: Request,
    customer_id: str = Form(...),
    order_date: str = Form(...),
    currency_code: Optional[str] = Form(None),
    customer_po_number: Optional[str] = Form(None),
    requested_date: Optional[str] = Form(None),
    promised_date: Optional[str] = Form(None),
    payment_terms_id: Optional[str] = Form(None),
    ship_to_name: Optional[str] = Form(None),
    ship_to_address: Optional[str] = Form(None),
    ship_to_city: Optional[str] = Form(None),
    ship_to_state: Optional[str] = Form(None),
    ship_to_postal_code: Optional[str] = Form(None),
    ship_to_country: Optional[str] = Form(None),
    shipping_method: Optional[str] = Form(None),
    allow_partial_shipment: Optional[str] = Form(None),
    customer_notes: Optional[str] = Form(None),
    internal_notes: Optional[str] = Form(None),
    lines_json: str = Form("[]"),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Create new sales order."""
    import json

    try:
        lines = json.loads(lines_json) if lines_json else []

        if not currency_code:
            currency_code = org_context_service.get_functional_currency(
                db,
                auth.organization_id,
            )

        so = sales_order_service.create(
            db,
            organization_id=str(auth.organization_id),
            customer_id=customer_id,
            order_date=datetime.strptime(order_date, "%Y-%m-%d").date(),
            created_by=str(auth.user_id),
            currency_code=currency_code,
            customer_po_number=customer_po_number,
            requested_date=datetime.strptime(requested_date, "%Y-%m-%d").date() if requested_date else None,
            promised_date=datetime.strptime(promised_date, "%Y-%m-%d").date() if promised_date else None,
            payment_terms_id=payment_terms_id if payment_terms_id else None,
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
            lines=lines,
        )
        db.commit()
        return RedirectResponse(f"/sales-orders/{so.so_id}", status_code=303)
    except Exception as e:
        db.rollback()
        org_id = coerce_uuid(str(auth.organization_id))
        context = base_context(request, auth, "New Sales Order", "sales-orders")
        context.update(_get_form_context(db, org_id))
        context["order"] = None
        context["error"] = str(e)
        return templates.TemplateResponse(request, "ifrs/ar/sales_order_form.html", context)


# =============================================================================
# Sales Order Detail
# =============================================================================

@router.get("/{so_id}", response_class=HTMLResponse)
def sales_order_detail(
    request: Request,
    so_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Sales order detail page."""
    org_id = coerce_uuid(str(auth.organization_id))
    so = db.get(SalesOrder, coerce_uuid(so_id))

    if not so or so.organization_id != org_id:
        context = base_context(request, auth, "Sales Order Not Found", "sales-orders")
        context["order"] = None
        return templates.TemplateResponse(request, "ifrs/ar/sales_order_detail.html", context)

    # Format order data
    order_data = {
        "so_id": str(so.so_id),
        "so_number": so.so_number,
        "order_date": _format_date(so.order_date),
        "customer_name": so.customer.customer_name if so.customer else "-",
        "customer_po": so.customer_po_number or "-",
        "reference": so.reference or "-",
        "requested_date": _format_date(so.requested_date) if so.requested_date else "-",
        "promised_date": _format_date(so.promised_date) if so.promised_date else "-",
        "subtotal": _format_currency(so.subtotal, so.currency_code),
        "discount_amount": _format_currency(so.discount_amount, so.currency_code),
        "tax_amount": _format_currency(so.tax_amount, so.currency_code),
        "shipping_amount": _format_currency(so.shipping_amount, so.currency_code),
        "total_amount": _format_currency(so.total_amount, so.currency_code),
        "invoiced_amount": _format_currency(so.invoiced_amount, so.currency_code),
        "currency_code": so.currency_code,
        "status": so.status.value,
        "is_fully_shipped": so.is_fully_shipped,
        "is_fully_invoiced": so.is_fully_invoiced,
        "customer_notes": so.customer_notes or "",
        "internal_notes": so.internal_notes or "",
        "payment_terms": so.payment_terms.term_name if so.payment_terms else "-",
        # Shipping
        "ship_to_name": so.ship_to_name or "-",
        "ship_to_address": so.ship_to_address or "",
        "ship_to_city": so.ship_to_city or "",
        "ship_to_state": so.ship_to_state or "",
        "ship_to_postal_code": so.ship_to_postal_code or "",
        "ship_to_country": so.ship_to_country or "",
        "shipping_method": so.shipping_method or "-",
        "allow_partial": so.allow_partial_shipment,
        # Timestamps
        "submitted_at": so.submitted_at.strftime("%Y-%m-%d %H:%M") if so.submitted_at else None,
        "approved_at": so.approved_at.strftime("%Y-%m-%d %H:%M") if so.approved_at else None,
        "confirmed_at": so.confirmed_at.strftime("%Y-%m-%d %H:%M") if so.confirmed_at else None,
        "completed_at": so.completed_at.strftime("%Y-%m-%d %H:%M") if so.completed_at else None,
        "cancelled_at": so.cancelled_at.strftime("%Y-%m-%d %H:%M") if so.cancelled_at else None,
        "cancellation_reason": so.cancellation_reason,
        "created_at": so.created_at.strftime("%Y-%m-%d %H:%M") if so.created_at else "",
        # Actions
        "can_submit": so.status == SOStatus.DRAFT,
        "can_approve": so.status == SOStatus.SUBMITTED,
        "can_confirm": so.status == SOStatus.APPROVED,
        "can_ship": so.status in [SOStatus.CONFIRMED, SOStatus.IN_PROGRESS] and not so.is_fully_shipped,
        "can_invoice": so.status in [SOStatus.IN_PROGRESS, SOStatus.SHIPPED] and not so.is_fully_invoiced,
        "can_cancel": so.status not in [SOStatus.SHIPPED, SOStatus.COMPLETED, SOStatus.CANCELLED],
        "can_hold": so.status not in [SOStatus.COMPLETED, SOStatus.CANCELLED, SOStatus.ON_HOLD],
        "can_release": so.status == SOStatus.ON_HOLD,
    }

    # Lines
    lines = []
    for line in so.lines:
        lines.append({
            "line_id": str(line.line_id),
            "line_number": line.line_number,
            "item_code": line.item_code or "-",
            "description": line.description,
            "quantity_ordered": str(line.quantity_ordered),
            "quantity_shipped": str(line.quantity_shipped),
            "quantity_invoiced": str(line.quantity_invoiced),
            "unit_price": _format_currency(line.unit_price, so.currency_code),
            "discount": _format_currency(line.discount_amount, so.currency_code),
            "tax": _format_currency(line.tax_amount, so.currency_code),
            "line_total": _format_currency(line.line_total, so.currency_code),
            "fulfillment_status": line.fulfillment_status.value,
            "can_ship": line.quantity_shipped < line.quantity_ordered and so.status in [SOStatus.CONFIRMED, SOStatus.IN_PROGRESS],
        })

    # Shipments
    shipments = []
    for ship in so.shipments:
        shipments.append({
            "shipment_id": str(ship.shipment_id),
            "shipment_number": ship.shipment_number,
            "shipment_date": _format_date(ship.shipment_date),
            "carrier": ship.carrier or "-",
            "tracking_number": ship.tracking_number or "-",
            "is_delivered": ship.is_delivered,
            "delivered_at": ship.delivered_at.strftime("%Y-%m-%d %H:%M") if ship.delivered_at else None,
        })

    context = base_context(request, auth, f"SO {so.so_number}", "sales-orders")
    context["order"] = order_data
    context["lines"] = lines
    context["shipments"] = shipments
    return templates.TemplateResponse(request, "ifrs/ar/sales_order_detail.html", context)


# =============================================================================
# Sales Order Actions
# =============================================================================

@router.post("/{so_id}/submit", response_class=HTMLResponse)
def submit_order(
    request: Request,
    so_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Submit sales order for approval."""
    try:
        sales_order_service.submit(db, so_id, str(auth.user_id))
        db.commit()
    except Exception:
        db.rollback()
    return RedirectResponse(f"/sales-orders/{so_id}", status_code=303)


@router.post("/{so_id}/approve", response_class=HTMLResponse)
def approve_order(
    request: Request,
    so_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Approve sales order."""
    try:
        sales_order_service.approve(db, so_id, str(auth.user_id))
        db.commit()
    except Exception:
        db.rollback()
    return RedirectResponse(f"/sales-orders/{so_id}", status_code=303)


@router.post("/{so_id}/confirm", response_class=HTMLResponse)
def confirm_order(
    request: Request,
    so_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Confirm sales order."""
    try:
        sales_order_service.confirm(db, so_id)
        db.commit()
    except Exception:
        db.rollback()
    return RedirectResponse(f"/sales-orders/{so_id}", status_code=303)


@router.post("/{so_id}/cancel", response_class=HTMLResponse)
def cancel_order(
    request: Request,
    so_id: str,
    reason: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Cancel sales order."""
    try:
        sales_order_service.cancel(db, so_id, str(auth.user_id), reason)
        db.commit()
    except Exception:
        db.rollback()
    return RedirectResponse(f"/sales-orders/{so_id}", status_code=303)


@router.post("/{so_id}/hold", response_class=HTMLResponse)
def hold_order(
    request: Request,
    so_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Put sales order on hold."""
    try:
        sales_order_service.hold(db, so_id, str(auth.user_id))
        db.commit()
    except Exception:
        db.rollback()
    return RedirectResponse(f"/sales-orders/{so_id}", status_code=303)


@router.post("/{so_id}/release", response_class=HTMLResponse)
def release_order(
    request: Request,
    so_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Release sales order from hold."""
    try:
        sales_order_service.release_hold(db, so_id, str(auth.user_id))
        db.commit()
    except Exception:
        db.rollback()
    return RedirectResponse(f"/sales-orders/{so_id}", status_code=303)


@router.post("/{so_id}/create-invoice", response_class=HTMLResponse)
def create_invoice_from_order(
    request: Request,
    so_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Create invoice from shipped lines."""
    try:
        invoice = sales_order_service.create_invoice_from_so(
            db, so_id, str(auth.user_id)
        )
        db.commit()
        return RedirectResponse(f"/ar/invoices/{invoice.invoice_id}", status_code=303)
    except Exception:
        db.rollback()
        return RedirectResponse(f"/sales-orders/{so_id}", status_code=303)


# =============================================================================
# Shipment Actions
# =============================================================================

@router.get("/{so_id}/ship", response_class=HTMLResponse)
def ship_order_form(
    request: Request,
    so_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Shipment form for sales order."""
    org_id = coerce_uuid(str(auth.organization_id))
    so = db.get(SalesOrder, coerce_uuid(so_id))

    if not so or so.organization_id != org_id:
        return RedirectResponse("/sales-orders", status_code=303)

    # Get lines available for shipping
    lines = []
    for line in so.lines:
        remaining = line.quantity_ordered - line.quantity_shipped
        if remaining > 0:
            lines.append({
                "line_id": str(line.line_id),
                "line_number": line.line_number,
                "item_code": line.item_code or "-",
                "description": line.description,
                "quantity_ordered": str(line.quantity_ordered),
                "quantity_shipped": str(line.quantity_shipped),
                "remaining": str(remaining),
            })

    context = base_context(request, auth, f"Ship SO {so.so_number}", "sales-orders")
    context["order"] = {
        "so_id": str(so.so_id),
        "so_number": so.so_number,
        "customer_name": so.customer.customer_name if so.customer else "-",
        "ship_to_name": so.ship_to_name or "-",
        "ship_to_address": so.ship_to_address or "",
        "shipping_method": so.shipping_method or "",
    }
    context["lines"] = lines
    context["today"] = _format_date(date.today())

    return templates.TemplateResponse(request, "ifrs/ar/shipment_form.html", context)


@router.post("/{so_id}/ship", response_class=HTMLResponse)
def create_shipment(
    request: Request,
    so_id: str,
    shipment_date: str = Form(...),
    carrier: Optional[str] = Form(None),
    tracking_number: Optional[str] = Form(None),
    shipping_method: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    line_quantities_json: str = Form("[]"),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Create shipment for sales order."""
    import json

    try:
        line_quantities = json.loads(line_quantities_json) if line_quantities_json else []

        # Filter out zero quantities
        line_quantities = [lq for lq in line_quantities if Decimal(str(lq.get("quantity", 0))) > 0]

        if not line_quantities:
            raise ValueError("No items to ship")

        shipment = sales_order_service.create_shipment(
            db,
            so_id=so_id,
            shipment_date=datetime.strptime(shipment_date, "%Y-%m-%d").date(),
            created_by=str(auth.user_id),
            line_quantities=line_quantities,
            carrier=carrier,
            tracking_number=tracking_number,
            shipping_method=shipping_method,
            notes=notes,
        )
        db.commit()
        return RedirectResponse(f"/sales-orders/{so_id}", status_code=303)
    except Exception as e:
        db.rollback()
        return RedirectResponse(f"/sales-orders/{so_id}/ship?error={str(e)}", status_code=303)


@router.post("/shipments/{shipment_id}/deliver", response_class=HTMLResponse)
def mark_delivered(
    request: Request,
    shipment_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Mark shipment as delivered."""
    try:
        shipment = sales_order_service.mark_delivered(db, shipment_id)
        db.commit()
        return RedirectResponse(f"/sales-orders/{shipment.so_id}", status_code=303)
    except Exception:
        db.rollback()
        return RedirectResponse("/sales-orders", status_code=303)
