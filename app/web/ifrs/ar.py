"""
AR (Accounts Receivable) Web Routes.

HTML template routes for Customers, Invoices, and Receipts.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.web.deps import get_db, require_web_auth, WebAuthContext, base_context
from app.services.ifrs.ar.customer import customer_service
from app.services.ifrs.ar.invoice import ar_invoice_service
from app.services.ifrs.ar.web import ar_web_service

templates = Jinja2Templates(directory="templates")

router = APIRouter(prefix="/ar", tags=["ar-web"])


# =============================================================================
# Customers
# =============================================================================

@router.get("/customers", response_class=HTMLResponse)
def list_customers(
    request: Request,
    search: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Customers list page."""
    context = base_context(request, auth, "Customers", "ar")
    context.update(
        ar_web_service.list_customers_context(
            db,
            str(auth.organization_id),
            search=search,
            status=status,
            page=page,
        )
    )
    return templates.TemplateResponse(request, "ifrs/ar/customers.html", context)


@router.get("/customers/new", response_class=HTMLResponse)
def new_customer_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New customer form page."""
    context = base_context(request, auth, "New Customer", "ar")
    context.update(ar_web_service.customer_form_context(db, str(auth.organization_id)))
    return templates.TemplateResponse(request, "ifrs/ar/customer_form.html", context)


@router.get("/customers/{customer_id}", response_class=HTMLResponse)
def view_customer(
    request: Request,
    customer_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Customer detail page."""
    # In real app, call customer_service.get()
    customer = None

    context = base_context(request, auth, "Customer Details", "ar")
    context["customer"] = customer

    return templates.TemplateResponse(request, "ifrs/ar/customer_detail.html", context)


@router.get("/customers/{customer_id}/edit", response_class=HTMLResponse)
def edit_customer_form(
    request: Request,
    customer_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Edit customer form page."""
    context = base_context(request, auth, "Edit Customer", "ar")
    context.update(ar_web_service.customer_form_context(db, str(auth.organization_id), customer_id))

    return templates.TemplateResponse(request, "ifrs/ar/customer_form.html", context)


@router.post("/customers/new")
async def create_customer(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Handle customer form submission."""
    form_data = await request.form()

    try:
        input_data = ar_web_service.build_customer_input(dict(form_data))

        customer = customer_service.create_customer(
            db=db,
            organization_id=auth.organization_id,
            input=input_data,
        )

        return RedirectResponse(
            url="/ar/customers?success=Customer+created+successfully",
            status_code=303,
        )

    except Exception as e:
        context = base_context(request, auth, "New Customer", "ar")
        context.update(ar_web_service.customer_form_context(db, str(auth.organization_id)))
        context["error"] = str(e)
        context["form_data"] = dict(form_data)
        return templates.TemplateResponse(request, "ifrs/ar/customer_form.html", context)


@router.post("/customers/{customer_id}/edit")
async def update_customer(
    request: Request,
    customer_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Handle customer update form submission."""
    form_data = await request.form()

    try:
        input_data = ar_web_service.build_customer_input(dict(form_data))

        customer = customer_service.update_customer(
            db=db,
            organization_id=auth.organization_id,
            customer_id=UUID(customer_id),
            input=input_data,
        )

        return RedirectResponse(
            url="/ar/customers?success=Customer+updated+successfully",
            status_code=303,
        )

    except Exception as e:
        context = base_context(request, auth, "Edit Customer", "ar")
        context.update(ar_web_service.customer_form_context(db, str(auth.organization_id), customer_id))
        context["error"] = str(e)
        context["form_data"] = dict(form_data)
        return templates.TemplateResponse(request, "ifrs/ar/customer_form.html", context)


# =============================================================================
# AR Invoices
# =============================================================================

@router.get("/invoices", response_class=HTMLResponse)
def list_invoices(
    request: Request,
    search: Optional[str] = None,
    customer_id: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """AR invoices list page."""
    context = base_context(request, auth, "AR Invoices", "ar")
    context.update(
        ar_web_service.list_invoices_context(
            db,
            str(auth.organization_id),
            search=search,
            customer_id=customer_id,
            status=status,
            start_date=start_date,
            end_date=end_date,
            page=page,
        )
    )
    return templates.TemplateResponse(request, "ifrs/ar/invoices.html", context)


@router.get("/invoices/new", response_class=HTMLResponse)
def new_invoice_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New AR invoice form page."""
    context = base_context(request, auth, "New AR Invoice", "ar")
    context.update(ar_web_service.invoice_form_context(db, str(auth.organization_id)))

    return templates.TemplateResponse(request, "ifrs/ar/invoice_form.html", context)


@router.post("/invoices/new")
async def create_invoice(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Handle AR invoice form submission."""

    # Check content type - handle both form and JSON
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        data = await request.json()
    else:
        form_data = await request.form()
        data = dict(form_data)

    try:
        input_data = ar_web_service.build_invoice_input(data)

        invoice = ar_invoice_service.create_invoice(
            db=db,
            organization_id=auth.organization_id,
            input=input_data,
            created_by_user_id=auth.user_id,
        )

        # Return JSON response for AJAX, redirect for form
        if "application/json" in content_type:
            return {"success": True, "invoice_id": str(invoice.invoice_id)}

        return RedirectResponse(
            url="/ar/invoices?success=Invoice+created+successfully",
            status_code=303,
        )

    except Exception as e:
        if "application/json" in content_type:
            return JSONResponse(
                status_code=400,
                content={"detail": str(e)},
            )

        context = base_context(request, auth, "New AR Invoice", "ar")
        context.update(ar_web_service.invoice_form_context(db, str(auth.organization_id)))
        context["error"] = str(e)
        context["form_data"] = data
        return templates.TemplateResponse(request, "ifrs/ar/invoice_form.html", context)


@router.get("/invoices/{invoice_id}", response_class=HTMLResponse)
def view_invoice(
    request: Request,
    invoice_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """AR invoice detail page."""
    # In real app, call ar_invoice_service.get()
    invoice = None

    context = base_context(request, auth, "AR Invoice Details", "ar")
    context["invoice"] = invoice

    return templates.TemplateResponse(request, "ifrs/ar/invoice_detail.html", context)


# =============================================================================
# AR Receipts
# =============================================================================

@router.get("/receipts", response_class=HTMLResponse)
def list_receipts(
    request: Request,
    search: Optional[str] = None,
    customer_id: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """AR receipts list page."""
    context = base_context(request, auth, "AR Receipts", "ar")
    context.update(
        ar_web_service.list_receipts_context(
            db,
            str(auth.organization_id),
            search=search,
            customer_id=customer_id,
            status=status,
            start_date=start_date,
            end_date=end_date,
            page=page,
        )
    )
    return templates.TemplateResponse(request, "ifrs/ar/receipts.html", context)


@router.get("/receipts/new", response_class=HTMLResponse)
def new_receipt_form(
    request: Request,
    invoice_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New AR receipt form page."""
    # In real app, fetch customers and open invoices
    customers_list = []
    invoice = None

    context = base_context(request, auth, "New AR Receipt", "ar")
    context.update({
        "customers_list": customers_list,
        "invoice_id": invoice_id,
        "invoice": invoice,
    })

    return templates.TemplateResponse(request, "ifrs/ar/receipt_form.html", context)


@router.get("/receipts/{receipt_id}", response_class=HTMLResponse)
def view_receipt(
    request: Request,
    receipt_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """AR receipt detail page."""
    # In real app, call customer_payment_service.get()
    receipt = None

    context = base_context(request, auth, "AR Receipt Details", "ar")
    context["receipt"] = receipt

    return templates.TemplateResponse(request, "ifrs/ar/receipt_detail.html", context)


# =============================================================================
# AR Aging Report
# =============================================================================

@router.get("/aging", response_class=HTMLResponse)
def aging_report(
    request: Request,
    as_of_date: Optional[str] = None,
    customer_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """AR aging report page."""
    context = base_context(request, auth, "AR Aging Report", "ar")
    context.update(
        ar_web_service.aging_context(
            db,
            str(auth.organization_id),
            as_of_date=as_of_date,
            customer_id=customer_id,
        )
    )
    return templates.TemplateResponse(request, "ifrs/ar/aging.html", context)
