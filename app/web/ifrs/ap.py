"""
AP (Accounts Payable) Web Routes.

HTML template routes for Suppliers, Invoices, and Payments.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.web.deps import get_db, require_web_auth, WebAuthContext, base_context
from app.models.ifrs.common.attachment import AttachmentCategory
from app.services.ifrs.common.attachment import attachment_service, AttachmentInput
from app.models.ifrs.ap.payment_batch import APBatchStatus
from app.models.ifrs.ap.supplier import Supplier
from app.models.ifrs.ap.supplier_invoice import SupplierInvoice
from app.models.ifrs.ap.supplier_payment import APPaymentMethod
from app.models.ifrs.banking.bank_account import BankAccountStatus
from app.services.ifrs.ap.payment_batch import payment_batch_service
from app.services.ifrs.ap.supplier import supplier_service
from app.services.ifrs.ap.supplier_invoice import supplier_invoice_service
from app.services.ifrs.ap.web import ap_web_service
from app.services.ifrs.banking.bank_account import bank_account_service

templates = Jinja2Templates(directory="templates")

router = APIRouter(prefix="/ap", tags=["ap-web"])


# =============================================================================
# Suppliers
# =============================================================================

@router.get("/suppliers", response_class=HTMLResponse)
def list_suppliers(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    search: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Suppliers list page."""
    context = base_context(request, auth, "Suppliers", "ap")
    context.update(
        ap_web_service.list_suppliers_context(
            db,
            str(auth.organization_id),
            search=search,
            status=status,
            page=page,
        )
    )
    return templates.TemplateResponse(request, "ifrs/ap/suppliers.html", context)


@router.get("/suppliers/new", response_class=HTMLResponse)
def new_supplier_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New supplier form page."""
    context = base_context(request, auth, "New Supplier", "ap")
    context.update(ap_web_service.supplier_form_context(db, str(auth.organization_id)))
    return templates.TemplateResponse(request, "ifrs/ap/supplier_form.html", context)


@router.get("/suppliers/{supplier_id}", response_class=HTMLResponse)
def view_supplier(
    request: Request,
    supplier_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Supplier detail page."""
    context = base_context(request, auth, "Supplier Details", "ap")
    context.update(
        ap_web_service.supplier_detail_context(
            db,
            str(auth.organization_id),
            supplier_id,
        )
    )

    return templates.TemplateResponse(request, "ifrs/ap/supplier_detail.html", context)


@router.get("/suppliers/{supplier_id}/edit", response_class=HTMLResponse)
def edit_supplier_form(
    request: Request,
    supplier_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Edit supplier form page."""
    context = base_context(request, auth, "Edit Supplier", "ap")
    context.update(ap_web_service.supplier_form_context(db, str(auth.organization_id), supplier_id))

    return templates.TemplateResponse(request, "ifrs/ap/supplier_form.html", context)


@router.post("/suppliers/new")
async def create_supplier(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Handle supplier form submission."""
    form_data = await request.form()

    try:
        input_data = ap_web_service.build_supplier_input(dict(form_data))

        supplier = supplier_service.create_supplier(
            db=db,
            organization_id=auth.organization_id,
            input=input_data,
        )

        # Redirect to suppliers list on success
        return RedirectResponse(
            url="/ap/suppliers?success=Supplier+created+successfully",
            status_code=303,
        )

    except Exception as e:
        # Re-render form with error
        context = base_context(request, auth, "New Supplier", "ap")
        context.update(ap_web_service.supplier_form_context(db, str(auth.organization_id)))
        context["error"] = str(e)
        context["form_data"] = dict(form_data)
        return templates.TemplateResponse(request, "ifrs/ap/supplier_form.html", context)


@router.post("/suppliers/{supplier_id}/edit")
async def update_supplier(
    request: Request,
    supplier_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Handle supplier update form submission."""
    form_data = await request.form()

    try:
        input_data = ap_web_service.build_supplier_input(dict(form_data))

        supplier = supplier_service.update_supplier(
            db=db,
            organization_id=auth.organization_id,
            supplier_id=UUID(supplier_id),
            input=input_data,
        )

        return RedirectResponse(
            url="/ap/suppliers?success=Supplier+updated+successfully",
            status_code=303,
        )

    except Exception as e:
        context = base_context(request, auth, "Edit Supplier", "ap")
        context.update(ap_web_service.supplier_form_context(db, str(auth.organization_id), supplier_id))
        context["error"] = str(e)
        context["form_data"] = dict(form_data)
        return templates.TemplateResponse(request, "ifrs/ap/supplier_form.html", context)


@router.post("/suppliers/{supplier_id}/delete")
def delete_supplier(
    request: Request,
    supplier_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Delete a supplier."""
    error = ap_web_service.delete_supplier(db, str(auth.organization_id), supplier_id)

    if error:
        context = base_context(request, auth, "Supplier Details", "ap")
        context.update(
            ap_web_service.supplier_detail_context(
                db, str(auth.organization_id), supplier_id
            )
        )
        context["error"] = error
        return templates.TemplateResponse(request, "ifrs/ap/supplier_detail.html", context)

    return RedirectResponse(url="/ap/suppliers", status_code=303)


# =============================================================================
# AP Invoices
# =============================================================================

@router.get("/invoices", response_class=HTMLResponse)
def list_invoices(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    search: Optional[str] = None,
    supplier_id: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """AP invoices list page."""
    context = base_context(request, auth, "AP Invoices", "ap")
    context.update(
        ap_web_service.list_invoices_context(
            db,
            str(auth.organization_id),
            search=search,
            supplier_id=supplier_id,
            status=status,
            start_date=start_date,
            end_date=end_date,
            page=page,
        )
    )
    return templates.TemplateResponse(request, "ifrs/ap/invoices.html", context)


@router.get("/invoices/new", response_class=HTMLResponse)
def new_invoice_form(
    request: Request,
    supplier_id: Optional[str] = None,
    po_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New AP invoice form page."""
    context = base_context(request, auth, "New AP Invoice", "ap")
    context.update(ap_web_service.invoice_form_context(
        db, str(auth.organization_id), supplier_id=supplier_id, po_id=po_id
    ))

    return templates.TemplateResponse(request, "ifrs/ap/invoice_form.html", context)


@router.post("/invoices/new")
async def create_invoice(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Handle AP invoice form submission."""

    # Check content type - handle both form and JSON
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        data = await request.json()
    else:
        form_data = await request.form()
        data = dict(form_data)

    try:
        input_data = ap_web_service.build_invoice_input(data)

        invoice = supplier_invoice_service.create_invoice(
            db=db,
            organization_id=auth.organization_id,
            input=input_data,
            created_by_user_id=auth.person_id,
        )

        # Return JSON response for AJAX, redirect for form
        if "application/json" in content_type:
            return {"success": True, "invoice_id": str(invoice.invoice_id)}

        return RedirectResponse(
            url="/ap/invoices?success=Invoice+created+successfully",
            status_code=303,
        )

    except Exception as e:
        if "application/json" in content_type:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=400,
                content={"detail": str(e)},
            )

        context = base_context(request, auth, "New AP Invoice", "ap")
        context.update(ap_web_service.invoice_form_context(db, str(auth.organization_id)))
        context["error"] = str(e)
        context["form_data"] = data
        return templates.TemplateResponse(request, "ifrs/ap/invoice_form.html", context)


@router.get("/invoices/{invoice_id}", response_class=HTMLResponse)
def view_invoice(
    request: Request,
    invoice_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """AP invoice detail page."""
    context = base_context(request, auth, "AP Invoice Details", "ap")
    context.update(
        ap_web_service.invoice_detail_context(
            db,
            str(auth.organization_id),
            invoice_id,
        )
    )

    return templates.TemplateResponse(request, "ifrs/ap/invoice_detail.html", context)


@router.post("/invoices/{invoice_id}/delete")
def delete_invoice(
    request: Request,
    invoice_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Delete an AP invoice."""
    error = ap_web_service.delete_invoice(db, str(auth.organization_id), invoice_id)

    if error:
        context = base_context(request, auth, "AP Invoice Details", "ap")
        context.update(
            ap_web_service.invoice_detail_context(
                db, str(auth.organization_id), invoice_id
            )
        )
        context["error"] = error
        return templates.TemplateResponse(request, "ifrs/ap/invoice_detail.html", context)

    return RedirectResponse(url="/ap/invoices", status_code=303)


# =============================================================================
# AP Payments
# =============================================================================

@router.get("/payments", response_class=HTMLResponse)
def list_payments(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    search: Optional[str] = None,
    supplier_id: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """AP payments list page."""
    context = base_context(request, auth, "AP Payments", "ap")
    context.update(
        ap_web_service.list_payments_context(
            db,
            str(auth.organization_id),
            search=search,
            supplier_id=supplier_id,
            status=status,
            start_date=start_date,
            end_date=end_date,
            page=page,
        )
    )
    return templates.TemplateResponse(request, "ifrs/ap/payments.html", context)


@router.get("/payments/new", response_class=HTMLResponse)
def new_payment_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New AP payment form page."""
    context = base_context(request, auth, "New AP Payment", "ap")
    context.update(
        ap_web_service.payment_form_context(
            db,
            str(auth.organization_id),
        )
    )

    return templates.TemplateResponse(request, "ifrs/ap/payment_form.html", context)


@router.get("/payments/{payment_id}", response_class=HTMLResponse)
def view_payment(
    request: Request,
    payment_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """AP payment detail page."""
    context = base_context(request, auth, "AP Payment Details", "ap")
    context.update(
        ap_web_service.payment_detail_context(
            db,
            str(auth.organization_id),
            payment_id,
        )
    )

    return templates.TemplateResponse(request, "ifrs/ap/payment_detail.html", context)


@router.post("/payments/new")
async def create_payment(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Handle AP payment form submission."""
    from fastapi.responses import JSONResponse

    # Check content type - handle both form and JSON
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        data = await request.json()
    else:
        form_data = await request.form()
        data = dict(form_data)

    try:
        input_data = ap_web_service.build_payment_input(data)

        from app.services.ifrs.ap.supplier_payment import supplier_payment_service
        payment = supplier_payment_service.create_payment(
            db=db,
            organization_id=auth.organization_id,
            input=input_data,
            created_by_user_id=auth.person_id,
        )

        # Return JSON response for AJAX, redirect for form
        if "application/json" in content_type:
            return {"success": True, "payment_id": str(payment.payment_id)}

        return RedirectResponse(
            url="/ap/payments?success=Payment+created+successfully",
            status_code=303,
        )

    except Exception as e:
        if "application/json" in content_type:
            return JSONResponse(
                status_code=400,
                content={"detail": str(e)},
            )

        context = base_context(request, auth, "New AP Payment", "ap")
        context.update(ap_web_service.payment_form_context(db, str(auth.organization_id)))
        context["error"] = str(e)
        context["form_data"] = data
        return templates.TemplateResponse(request, "ifrs/ap/payment_form.html", context)


@router.post("/payments/{payment_id}/delete")
def delete_payment(
    request: Request,
    payment_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Delete an AP payment."""
    error = ap_web_service.delete_payment(db, str(auth.organization_id), payment_id)

    if error:
        context = base_context(request, auth, "AP Payment Details", "ap")
        context.update(
            ap_web_service.payment_detail_context(
                db, str(auth.organization_id), payment_id
            )
        )
        context["error"] = error
        return templates.TemplateResponse(request, "ifrs/ap/payment_detail.html", context)

    return RedirectResponse(url="/ap/payments", status_code=303)


# =============================================================================
# AP Payment Batches
# =============================================================================

@router.get("/payment-batches", response_class=HTMLResponse)
def list_payment_batches(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Payment batches list page."""
    status_value = None
    if status:
        try:
            status_value = APBatchStatus(status)
        except ValueError:
            status_value = None

    limit = 50
    offset = (page - 1) * limit
    batches = payment_batch_service.list(
        db=db,
        organization_id=str(auth.organization_id),
        status=status_value,
        limit=limit,
        offset=offset,
    )

    context = base_context(request, auth, "Payment Batches", "ap")
    context.update({
        "batches": batches,
        "status": status or "",
        "page": page,
    })
    return templates.TemplateResponse(request, "ifrs/ap/payment_batches.html", context)


@router.get("/payment-batches/new", response_class=HTMLResponse)
def new_payment_batch_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New payment batch form page."""
    bank_accounts = bank_account_service.list(
        db=db,
        organization_id=auth.organization_id,
        status=BankAccountStatus.active,
        limit=200,
    )
    invoices = (
        db.query(SupplierInvoice, Supplier)
        .join(Supplier, SupplierInvoice.supplier_id == Supplier.supplier_id)
        .filter(SupplierInvoice.organization_id == auth.organization_id)
        .order_by(SupplierInvoice.invoice_date.desc())
        .limit(50)
        .all()
    )
    invoices_view = [
        {
            "invoice_id": invoice.invoice_id,
            "invoice_number": invoice.invoice_number,
            "supplier_name": supplier.trading_name or supplier.legal_name,
            "due_date": invoice.due_date,
            "amount": invoice.total_amount,
            "currency_code": invoice.currency_code,
        }
        for invoice, supplier in invoices
    ]

    context = base_context(request, auth, "New Payment Batch", "ap")
    context.update({
        "bank_accounts": bank_accounts,
        "invoices": invoices_view,
        "payment_methods": [method.value for method in APPaymentMethod],
    })
    return templates.TemplateResponse(request, "ifrs/ap/payment_batch_form.html", context)


# =============================================================================
# Purchase Orders
# =============================================================================

@router.get("/purchase-orders", response_class=HTMLResponse)
def list_purchase_orders(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    search: Optional[str] = None,
    supplier_id: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Purchase orders list page."""
    context = base_context(request, auth, "Purchase Orders", "ap")
    context.update(
        ap_web_service.list_purchase_orders_context(
            db,
            str(auth.organization_id),
            search=search,
            supplier_id=supplier_id,
            status=status,
            start_date=start_date,
            end_date=end_date,
            page=page,
        )
    )
    return templates.TemplateResponse(request, "ifrs/ap/purchase_orders.html", context)


@router.get("/purchase-orders/new", response_class=HTMLResponse)
def new_purchase_order_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New purchase order form page."""
    context = base_context(request, auth, "New Purchase Order", "ap")
    context.update(ap_web_service.purchase_order_form_context(db, str(auth.organization_id)))
    return templates.TemplateResponse(request, "ifrs/ap/purchase_order_form.html", context)


@router.get("/purchase-orders/{po_id}", response_class=HTMLResponse)
def view_purchase_order(
    request: Request,
    po_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Purchase order detail page."""
    context = base_context(request, auth, "Purchase Order Details", "ap")
    context.update(
        ap_web_service.purchase_order_detail_context(
            db,
            str(auth.organization_id),
            po_id,
        )
    )
    return templates.TemplateResponse(request, "ifrs/ap/purchase_order_detail.html", context)


@router.get("/purchase-orders/{po_id}/edit", response_class=HTMLResponse)
def edit_purchase_order_form(
    request: Request,
    po_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Edit purchase order form page."""
    context = base_context(request, auth, "Edit Purchase Order", "ap")
    context.update(ap_web_service.purchase_order_form_context(db, str(auth.organization_id), po_id))
    if not context.get("order"):
        return RedirectResponse(url="/ap/purchase-orders", status_code=303)
    return templates.TemplateResponse(request, "ifrs/ap/purchase_order_form.html", context)


@router.post("/purchase-orders/new")
async def create_purchase_order(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Handle purchase order form submission."""
    from fastapi.responses import JSONResponse
    import json
    from datetime import datetime
    from decimal import Decimal
    from app.services.ifrs.ap.purchase_order import purchase_order_service, PurchaseOrderInput, POLineInput

    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        data = await request.json()
    else:
        form_data = await request.form()
        data = dict(form_data)

    try:
        # Parse lines
        lines_data = data.get("lines", [])
        if isinstance(lines_data, str):
            lines_data = json.loads(lines_data)

        lines = []
        for line in lines_data:
            if line.get("description"):
                lines.append(POLineInput(
                    item_id=UUID(line["item_id"]) if line.get("item_id") else None,
                    description=line.get("description", ""),
                    quantity=Decimal(str(line.get("quantity", 1))),
                    unit_price=Decimal(str(line.get("unit_price", 0))),
                    expense_account_id=UUID(line["expense_account_id"]) if line.get("expense_account_id") else None,
                ))

        # Parse dates
        po_date_str = data.get("po_date")
        po_date = datetime.strptime(po_date_str, "%Y-%m-%d").date() if po_date_str else None

        expected_delivery_str = data.get("expected_delivery_date")
        expected_delivery = datetime.strptime(expected_delivery_str, "%Y-%m-%d").date() if expected_delivery_str else None

        input_data = PurchaseOrderInput(
            supplier_id=UUID(data["supplier_id"]),
            po_date=po_date,
            expected_delivery_date=expected_delivery,
            currency_code=data.get("currency_code", "USD"),
            terms_and_conditions=data.get("terms_and_conditions"),
            lines=lines,
        )

        po = purchase_order_service.create_po(
            db=db,
            organization_id=auth.organization_id,
            input=input_data,
            created_by_user_id=auth.person_id,
        )

        if "application/json" in content_type:
            return {"success": True, "po_id": str(po.po_id)}

        return RedirectResponse(
            url=f"/ap/purchase-orders/{po.po_id}",
            status_code=303,
        )

    except Exception as e:
        if "application/json" in content_type:
            return JSONResponse(status_code=400, content={"detail": str(e)})

        context = base_context(request, auth, "New Purchase Order", "ap")
        context.update(ap_web_service.purchase_order_form_context(db, str(auth.organization_id)))
        context["error"] = str(e)
        context["form_data"] = data
        return templates.TemplateResponse(request, "ifrs/ap/purchase_order_form.html", context)


@router.post("/purchase-orders/{po_id}/submit")
def submit_purchase_order(
    request: Request,
    po_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Submit purchase order for approval."""
    from app.services.ifrs.ap.purchase_order import purchase_order_service

    try:
        purchase_order_service.submit_for_approval(
            db=db,
            organization_id=auth.organization_id,
            po_id=UUID(po_id),
        )
        return RedirectResponse(
            url=f"/ap/purchase-orders/{po_id}?success=Submitted+for+approval",
            status_code=303,
        )
    except Exception as e:
        context = base_context(request, auth, "Purchase Order Details", "ap")
        context.update(
            ap_web_service.purchase_order_detail_context(
                db, str(auth.organization_id), po_id
            )
        )
        context["error"] = str(e)
        return templates.TemplateResponse(request, "ifrs/ap/purchase_order_detail.html", context)


@router.post("/purchase-orders/{po_id}/approve")
def approve_purchase_order(
    request: Request,
    po_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Approve purchase order."""
    from app.services.ifrs.ap.purchase_order import purchase_order_service

    try:
        purchase_order_service.approve_po(
            db=db,
            organization_id=auth.organization_id,
            po_id=UUID(po_id),
            approved_by_user_id=auth.person_id,
        )
        return RedirectResponse(
            url=f"/ap/purchase-orders/{po_id}?success=Purchase+order+approved",
            status_code=303,
        )
    except Exception as e:
        context = base_context(request, auth, "Purchase Order Details", "ap")
        context.update(
            ap_web_service.purchase_order_detail_context(
                db, str(auth.organization_id), po_id
            )
        )
        context["error"] = str(e)
        return templates.TemplateResponse(request, "ifrs/ap/purchase_order_detail.html", context)


@router.post("/purchase-orders/{po_id}/cancel")
def cancel_purchase_order(
    request: Request,
    po_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Cancel purchase order."""
    from app.services.ifrs.ap.purchase_order import purchase_order_service

    try:
        purchase_order_service.cancel_po(
            db=db,
            organization_id=auth.organization_id,
            po_id=UUID(po_id),
        )
        return RedirectResponse(
            url=f"/ap/purchase-orders/{po_id}?success=Purchase+order+cancelled",
            status_code=303,
        )
    except Exception as e:
        context = base_context(request, auth, "Purchase Order Details", "ap")
        context.update(
            ap_web_service.purchase_order_detail_context(
                db, str(auth.organization_id), po_id
            )
        )
        context["error"] = str(e)
        return templates.TemplateResponse(request, "ifrs/ap/purchase_order_detail.html", context)


# =============================================================================
# Goods Receipts
# =============================================================================

@router.get("/goods-receipts", response_class=HTMLResponse)
def list_goods_receipts(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    search: Optional[str] = None,
    supplier_id: Optional[str] = None,
    po_id: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Goods receipts list page."""
    context = base_context(request, auth, "Goods Receipts", "ap")
    context.update(
        ap_web_service.list_goods_receipts_context(
            db,
            str(auth.organization_id),
            search=search,
            supplier_id=supplier_id,
            po_id=po_id,
            status=status,
            start_date=start_date,
            end_date=end_date,
            page=page,
        )
    )
    return templates.TemplateResponse(request, "ifrs/ap/goods_receipts.html", context)


@router.get("/goods-receipts/new", response_class=HTMLResponse)
def new_goods_receipt_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    po_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """New goods receipt form page."""
    context = base_context(request, auth, "New Goods Receipt", "ap")
    context.update(ap_web_service.goods_receipt_form_context(db, str(auth.organization_id), po_id))
    return templates.TemplateResponse(request, "ifrs/ap/goods_receipt_form.html", context)


@router.get("/goods-receipts/{receipt_id}", response_class=HTMLResponse)
def view_goods_receipt(
    request: Request,
    receipt_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Goods receipt detail page."""
    context = base_context(request, auth, "Goods Receipt Details", "ap")
    context.update(
        ap_web_service.goods_receipt_detail_context(
            db,
            str(auth.organization_id),
            receipt_id,
        )
    )
    return templates.TemplateResponse(request, "ifrs/ap/goods_receipt_detail.html", context)


@router.post("/goods-receipts/new")
async def create_goods_receipt(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Handle goods receipt form submission."""
    from fastapi.responses import JSONResponse
    import json
    from datetime import datetime
    from decimal import Decimal
    from app.services.ifrs.ap.goods_receipt import goods_receipt_service, GoodsReceiptInput, GRLineInput

    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        data = await request.json()
    else:
        form_data = await request.form()
        data = dict(form_data)

    try:
        # Parse lines
        lines_data = data.get("lines", [])
        if isinstance(lines_data, str):
            lines_data = json.loads(lines_data)

        lines = []
        for line in lines_data:
            qty = Decimal(str(line.get("quantity_to_receive", 0)))
            if qty > 0:
                lines.append(GRLineInput(
                    po_line_id=UUID(line["line_id"]),
                    quantity_received=qty,
                    lot_number=line.get("lot_number"),
                ))

        if not lines:
            raise ValueError("No items to receive")

        # Parse date
        receipt_date_str = data.get("receipt_date")
        receipt_date = datetime.strptime(receipt_date_str, "%Y-%m-%d").date() if receipt_date_str else None

        input_data = GoodsReceiptInput(
            po_id=UUID(data["po_id"]),
            receipt_date=receipt_date,
            warehouse_id=UUID(data["warehouse_id"]) if data.get("warehouse_id") else None,
            notes=data.get("notes"),
            lines=lines,
        )

        receipt = goods_receipt_service.create_receipt(
            db=db,
            organization_id=auth.organization_id,
            input=input_data,
            received_by_user_id=auth.person_id,
        )

        if "application/json" in content_type:
            return {"success": True, "receipt_id": str(receipt.receipt_id)}

        return RedirectResponse(
            url=f"/ap/goods-receipts/{receipt.receipt_id}",
            status_code=303,
        )

    except Exception as e:
        if "application/json" in content_type:
            return JSONResponse(status_code=400, content={"detail": str(e)})

        context = base_context(request, auth, "New Goods Receipt", "ap")
        context.update(ap_web_service.goods_receipt_form_context(
            db, str(auth.organization_id), data.get("po_id")
        ))
        context["error"] = str(e)
        context["form_data"] = data
        return templates.TemplateResponse(request, "ifrs/ap/goods_receipt_form.html", context)


@router.post("/goods-receipts/{receipt_id}/inspect")
def start_inspection(
    request: Request,
    receipt_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Start inspection on goods receipt."""
    from app.services.ifrs.ap.goods_receipt import goods_receipt_service

    try:
        goods_receipt_service.start_inspection(
            db=db,
            organization_id=auth.organization_id,
            receipt_id=UUID(receipt_id),
        )
        return RedirectResponse(
            url=f"/ap/goods-receipts/{receipt_id}?success=Inspection+started",
            status_code=303,
        )
    except Exception as e:
        context = base_context(request, auth, "Goods Receipt Details", "ap")
        context.update(
            ap_web_service.goods_receipt_detail_context(
                db, str(auth.organization_id), receipt_id
            )
        )
        context["error"] = str(e)
        return templates.TemplateResponse(request, "ifrs/ap/goods_receipt_detail.html", context)


@router.post("/goods-receipts/{receipt_id}/accept")
def accept_all(
    request: Request,
    receipt_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Accept all items on goods receipt."""
    from app.services.ifrs.ap.goods_receipt import goods_receipt_service

    try:
        goods_receipt_service.accept_all(
            db=db,
            organization_id=auth.organization_id,
            receipt_id=UUID(receipt_id),
        )
        return RedirectResponse(
            url=f"/ap/goods-receipts/{receipt_id}?success=All+items+accepted",
            status_code=303,
        )
    except Exception as e:
        context = base_context(request, auth, "Goods Receipt Details", "ap")
        context.update(
            ap_web_service.goods_receipt_detail_context(
                db, str(auth.organization_id), receipt_id
            )
        )
        context["error"] = str(e)
        return templates.TemplateResponse(request, "ifrs/ap/goods_receipt_detail.html", context)


# =============================================================================
# AP Aging Report
# =============================================================================

@router.get("/aging", response_class=HTMLResponse)
def aging_report(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    as_of_date: Optional[str] = None,
    supplier_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """AP aging report page."""
    context = base_context(request, auth, "AP Aging Report", "ap")
    context.update(
        ap_web_service.aging_context(
            db,
            str(auth.organization_id),
            as_of_date=as_of_date,
            supplier_id=supplier_id,
        )
    )
    return templates.TemplateResponse(request, "ifrs/ap/aging.html", context)


# =============================================================================
# Attachments
# =============================================================================

@router.post("/invoices/{invoice_id}/attachments")
async def upload_invoice_attachment(
    invoice_id: str,
    file: UploadFile = File(...),
    description: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Upload an attachment for a supplier invoice."""
    try:
        # Verify invoice exists and belongs to org
        invoice = supplier_invoice_service.get(db, invoice_id)
        if not invoice or invoice.organization_id != auth.organization_id:
            return RedirectResponse(
                url=f"/ap/invoices/{invoice_id}?error=Invoice+not+found",
                status_code=303,
            )

        # Create attachment input
        input_data = AttachmentInput(
            entity_type="SUPPLIER_INVOICE",
            entity_id=invoice_id,
            file_name=file.filename or "unnamed",
            content_type=file.content_type or "application/octet-stream",
            category=AttachmentCategory.INVOICE,
            description=description,
        )

        # Save the file
        attachment_service.save_file(
            db=db,
            organization_id=auth.organization_id,
            input=input_data,
            file_content=file.file,
            uploaded_by=auth.person_id,
        )

        return RedirectResponse(
            url=f"/ap/invoices/{invoice_id}?success=Attachment+uploaded",
            status_code=303,
        )

    except ValueError as e:
        return RedirectResponse(
            url=f"/ap/invoices/{invoice_id}?error={str(e)}",
            status_code=303,
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/ap/invoices/{invoice_id}?error=Upload+failed",
            status_code=303,
        )


@router.post("/purchase-orders/{po_id}/attachments")
async def upload_po_attachment(
    po_id: str,
    file: UploadFile = File(...),
    description: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Upload an attachment for a purchase order."""
    from app.services.ifrs.ap.purchase_order import purchase_order_service

    try:
        # Verify PO exists and belongs to org
        po = purchase_order_service.get(db, po_id)
        if not po or po.organization_id != auth.organization_id:
            return RedirectResponse(
                url=f"/ap/purchase-orders/{po_id}?error=Purchase+order+not+found",
                status_code=303,
            )

        input_data = AttachmentInput(
            entity_type="PURCHASE_ORDER",
            entity_id=po_id,
            file_name=file.filename or "unnamed",
            content_type=file.content_type or "application/octet-stream",
            category=AttachmentCategory.PURCHASE_ORDER,
            description=description,
        )

        attachment_service.save_file(
            db=db,
            organization_id=auth.organization_id,
            input=input_data,
            file_content=file.file,
            uploaded_by=auth.person_id,
        )

        return RedirectResponse(
            url=f"/ap/purchase-orders/{po_id}?success=Attachment+uploaded",
            status_code=303,
        )

    except ValueError as e:
        return RedirectResponse(
            url=f"/ap/purchase-orders/{po_id}?error={str(e)}",
            status_code=303,
        )
    except Exception:
        return RedirectResponse(
            url=f"/ap/purchase-orders/{po_id}?error=Upload+failed",
            status_code=303,
        )


@router.post("/goods-receipts/{receipt_id}/attachments")
async def upload_goods_receipt_attachment(
    receipt_id: str,
    file: UploadFile = File(...),
    description: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Upload an attachment for a goods receipt."""
    from app.services.ifrs.ap.goods_receipt import goods_receipt_service

    try:
        # Verify receipt exists and belongs to org
        receipt = goods_receipt_service.get(db, receipt_id)
        if not receipt or receipt.organization_id != auth.organization_id:
            return RedirectResponse(
                url=f"/ap/goods-receipts/{receipt_id}?error=Goods+receipt+not+found",
                status_code=303,
            )

        input_data = AttachmentInput(
            entity_type="GOODS_RECEIPT",
            entity_id=receipt_id,
            file_name=file.filename or "unnamed",
            content_type=file.content_type or "application/octet-stream",
            category=AttachmentCategory.GOODS_RECEIPT,
            description=description,
        )

        attachment_service.save_file(
            db=db,
            organization_id=auth.organization_id,
            input=input_data,
            file_content=file.file,
            uploaded_by=auth.person_id,
        )

        return RedirectResponse(
            url=f"/ap/goods-receipts/{receipt_id}?success=Attachment+uploaded",
            status_code=303,
        )

    except ValueError as e:
        return RedirectResponse(
            url=f"/ap/goods-receipts/{receipt_id}?error={str(e)}",
            status_code=303,
        )
    except Exception:
        return RedirectResponse(
            url=f"/ap/goods-receipts/{receipt_id}?error=Upload+failed",
            status_code=303,
        )


@router.post("/payments/{payment_id}/attachments")
async def upload_payment_attachment(
    payment_id: str,
    file: UploadFile = File(...),
    description: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Upload an attachment for a supplier payment."""
    from app.services.ifrs.ap.supplier_payment import supplier_payment_service

    try:
        # Verify payment exists and belongs to org
        payment = supplier_payment_service.get(db, payment_id)
        if not payment or payment.organization_id != auth.organization_id:
            return RedirectResponse(
                url=f"/ap/payments/{payment_id}?error=Payment+not+found",
                status_code=303,
            )

        input_data = AttachmentInput(
            entity_type="SUPPLIER_PAYMENT",
            entity_id=payment_id,
            file_name=file.filename or "unnamed",
            content_type=file.content_type or "application/octet-stream",
            category=AttachmentCategory.PAYMENT,
            description=description,
        )

        attachment_service.save_file(
            db=db,
            organization_id=auth.organization_id,
            input=input_data,
            file_content=file.file,
            uploaded_by=auth.person_id,
        )

        return RedirectResponse(
            url=f"/ap/payments/{payment_id}?success=Attachment+uploaded",
            status_code=303,
        )

    except ValueError as e:
        return RedirectResponse(
            url=f"/ap/payments/{payment_id}?error={str(e)}",
            status_code=303,
        )
    except Exception:
        return RedirectResponse(
            url=f"/ap/payments/{payment_id}?error=Upload+failed",
            status_code=303,
        )


@router.post("/suppliers/{supplier_id}/attachments")
async def upload_supplier_attachment(
    supplier_id: str,
    file: UploadFile = File(...),
    description: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Upload an attachment for a supplier."""
    try:
        # Verify supplier exists and belongs to org
        supplier = supplier_service.get(db, auth.organization_id, supplier_id)
        if not supplier or supplier.organization_id != auth.organization_id:
            return RedirectResponse(
                url=f"/ap/suppliers/{supplier_id}?error=Supplier+not+found",
                status_code=303,
            )

        input_data = AttachmentInput(
            entity_type="SUPPLIER",
            entity_id=supplier_id,
            file_name=file.filename or "unnamed",
            content_type=file.content_type or "application/octet-stream",
            category=AttachmentCategory.SUPPLIER,
            description=description,
        )

        attachment_service.save_file(
            db=db,
            organization_id=auth.organization_id,
            input=input_data,
            file_content=file.file,
            uploaded_by=auth.person_id,
        )

        return RedirectResponse(
            url=f"/ap/suppliers/{supplier_id}?success=Attachment+uploaded",
            status_code=303,
        )

    except ValueError as e:
        return RedirectResponse(
            url=f"/ap/suppliers/{supplier_id}?error={str(e)}",
            status_code=303,
        )
    except Exception:
        return RedirectResponse(
            url=f"/ap/suppliers/{supplier_id}?error=Upload+failed",
            status_code=303,
        )


@router.get("/attachments/{attachment_id}/download")
def download_attachment(
    attachment_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Download an attachment file."""
    attachment = attachment_service.get(db, auth.organization_id, attachment_id)

    if not attachment or attachment.organization_id != auth.organization_id:
        return RedirectResponse(url="/ap/invoices?error=Attachment+not+found", status_code=303)

    file_path = attachment_service.get_file_path(attachment)

    if not file_path.exists():
        return RedirectResponse(url="/ap/invoices?error=File+not+found", status_code=303)

    return FileResponse(
        path=str(file_path),
        filename=attachment.file_name,
        media_type=attachment.content_type,
    )


@router.post("/attachments/{attachment_id}/delete")
def delete_attachment(
    attachment_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Delete an attachment."""
    attachment = attachment_service.get(db, auth.organization_id, attachment_id)

    if not attachment or attachment.organization_id != auth.organization_id:
        return RedirectResponse(url="/ap/invoices?error=Attachment+not+found", status_code=303)

    # Get entity info for redirect
    entity_type = attachment.entity_type
    entity_id = attachment.entity_id

    # Delete the attachment
    attachment_service.delete(db, attachment_id, auth.organization_id)

    # Redirect based on entity type
    redirect_map = {
        "SUPPLIER_INVOICE": f"/ap/invoices/{entity_id}",
        "PURCHASE_ORDER": f"/ap/purchase-orders/{entity_id}",
        "GOODS_RECEIPT": f"/ap/goods-receipts/{entity_id}",
        "SUPPLIER_PAYMENT": f"/ap/payments/{entity_id}",
        "SUPPLIER": f"/ap/suppliers/{entity_id}",
    }

    redirect_url = redirect_map.get(entity_type, "/ap/invoices")
    return RedirectResponse(
        url=f"{redirect_url}?success=Attachment+deleted",
        status_code=303,
    )
