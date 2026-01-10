"""
AP (Accounts Payable) Web Routes.

HTML template routes for Suppliers, Invoices, and Payments.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.web.deps import get_db, require_web_auth, WebAuthContext, base_context
from app.services.ifrs.ap.supplier import supplier_service
from app.services.ifrs.ap.supplier_invoice import supplier_invoice_service
from app.services.ifrs.ap.web import ap_web_service

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
    # In real app, call supplier_service.get()
    supplier = None

    context = base_context(request, auth, "Supplier Details", "ap")
    context["supplier"] = supplier

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
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New AP invoice form page."""
    context = base_context(request, auth, "New AP Invoice", "ap")
    context.update(ap_web_service.invoice_form_context(db, str(auth.organization_id)))

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
    # In real app, call supplier_invoice_service.get()
    invoice = None

    context = base_context(request, auth, "AP Invoice Details", "ap")
    context["invoice"] = invoice

    return templates.TemplateResponse(request, "ifrs/ap/invoice_detail.html", context)


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
    # In real app, fetch suppliers and open invoices
    suppliers_list = []

    context = base_context(request, auth, "New AP Payment", "ap")
    context["suppliers_list"] = suppliers_list

    return templates.TemplateResponse(request, "ifrs/ap/payment_form.html", context)


@router.get("/payments/{payment_id}", response_class=HTMLResponse)
def view_payment(
    request: Request,
    payment_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """AP payment detail page."""
    # In real app, call supplier_payment_service.get()
    payment = None

    context = base_context(request, auth, "AP Payment Details", "ap")
    context["payment"] = payment

    return templates.TemplateResponse(request, "ifrs/ap/payment_detail.html", context)


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
