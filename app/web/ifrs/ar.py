"""
AR (Accounts Receivable) Web Routes.

HTML template routes for Customers, Invoices, and Receipts.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.web.deps import get_db, require_web_auth, WebAuthContext, base_context
from app.models.ifrs.common.attachment import AttachmentCategory
from app.services.ifrs.common.attachment import attachment_service, AttachmentInput
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
    context = base_context(request, auth, "Customer Details", "ar")
    context.update(
        ar_web_service.customer_detail_context(
            db,
            str(auth.organization_id),
            customer_id,
        )
    )

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


@router.post("/customers/{customer_id}/delete")
def delete_customer(
    request: Request,
    customer_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Delete a customer."""
    error = ar_web_service.delete_customer(db, str(auth.organization_id), customer_id)

    if error:
        context = base_context(request, auth, "Customer Details", "ar")
        context.update(
            ar_web_service.customer_detail_context(
                db, str(auth.organization_id), customer_id
            )
        )
        context["error"] = error
        return templates.TemplateResponse(request, "ifrs/ar/customer_detail.html", context)

    return RedirectResponse(url="/ar/customers", status_code=303)


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
    context = base_context(request, auth, "AR Invoice Details", "ar")
    context.update(
        ar_web_service.invoice_detail_context(
            db,
            str(auth.organization_id),
            invoice_id,
        )
    )

    return templates.TemplateResponse(request, "ifrs/ar/invoice_detail.html", context)


@router.post("/invoices/{invoice_id}/delete")
def delete_invoice(
    request: Request,
    invoice_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Delete an AR invoice."""
    error = ar_web_service.delete_invoice(db, str(auth.organization_id), invoice_id)

    if error:
        context = base_context(request, auth, "AR Invoice Details", "ar")
        context.update(
            ar_web_service.invoice_detail_context(
                db, str(auth.organization_id), invoice_id
            )
        )
        context["error"] = error
        return templates.TemplateResponse(request, "ifrs/ar/invoice_detail.html", context)

    return RedirectResponse(url="/ar/invoices", status_code=303)


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
    context = base_context(request, auth, "New AR Receipt", "ar")
    context.update(
        ar_web_service.receipt_form_context(
            db,
            str(auth.organization_id),
            invoice_id=invoice_id,
        )
    )

    return templates.TemplateResponse(request, "ifrs/ar/receipt_form.html", context)


@router.get("/receipts/{receipt_id}", response_class=HTMLResponse)
def view_receipt(
    request: Request,
    receipt_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """AR receipt detail page."""
    context = base_context(request, auth, "AR Receipt Details", "ar")
    context.update(
        ar_web_service.receipt_detail_context(
            db,
            str(auth.organization_id),
            receipt_id,
        )
    )

    return templates.TemplateResponse(request, "ifrs/ar/receipt_detail.html", context)


@router.post("/receipts/new")
async def create_receipt(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Handle AR receipt form submission."""
    from app.services.ifrs.ar.customer_payment import customer_payment_service

    # Check content type - handle both form and JSON
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        data = await request.json()
    else:
        form_data = await request.form()
        data = dict(form_data)

    try:
        input_data = ar_web_service.build_receipt_input(data)

        receipt = customer_payment_service.create_payment(
            db=db,
            organization_id=auth.organization_id,
            input=input_data,
            created_by_user_id=auth.user_id,
        )

        # Return JSON response for AJAX, redirect for form
        if "application/json" in content_type:
            return {"success": True, "receipt_id": str(receipt.payment_id)}

        return RedirectResponse(
            url="/ar/receipts?success=Receipt+created+successfully",
            status_code=303,
        )

    except Exception as e:
        if "application/json" in content_type:
            return JSONResponse(
                status_code=400,
                content={"detail": str(e)},
            )

        context = base_context(request, auth, "New AR Receipt", "ar")
        context.update(ar_web_service.receipt_form_context(db, str(auth.organization_id)))
        context["error"] = str(e)
        context["form_data"] = data
        return templates.TemplateResponse(request, "ifrs/ar/receipt_form.html", context)


@router.post("/receipts/{receipt_id}/delete")
def delete_receipt(
    request: Request,
    receipt_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Delete an AR receipt."""
    error = ar_web_service.delete_receipt(db, str(auth.organization_id), receipt_id)

    if error:
        context = base_context(request, auth, "AR Receipt Details", "ar")
        context.update(
            ar_web_service.receipt_detail_context(
                db, str(auth.organization_id), receipt_id
            )
        )
        context["error"] = error
        return templates.TemplateResponse(request, "ifrs/ar/receipt_detail.html", context)

    return RedirectResponse(url="/ar/receipts", status_code=303)


# =============================================================================
# AR Credit Notes
# =============================================================================

@router.get("/credit-notes", response_class=HTMLResponse)
def list_credit_notes(
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
    """AR credit notes list page."""
    context = base_context(request, auth, "AR Credit Notes", "ar")
    context.update(
        ar_web_service.list_credit_notes_context(
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
    return templates.TemplateResponse(request, "ifrs/ar/credit_notes.html", context)


@router.get("/credit-notes/new", response_class=HTMLResponse)
def new_credit_note_form(
    request: Request,
    invoice_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """New AR credit note form page."""
    context = base_context(request, auth, "New Credit Note", "ar")
    context.update(
        ar_web_service.credit_note_form_context(
            db,
            str(auth.organization_id),
            invoice_id=invoice_id,
        )
    )

    return templates.TemplateResponse(request, "ifrs/ar/credit_note_form.html", context)


@router.post("/credit-notes/new")
async def create_credit_note(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Handle AR credit note form submission."""

    # Check content type - handle both form and JSON
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        data = await request.json()
    else:
        form_data = await request.form()
        data = dict(form_data)

    try:
        input_data = ar_web_service.build_credit_note_input(data)

        credit_note = ar_invoice_service.create_invoice(
            db=db,
            organization_id=auth.organization_id,
            input=input_data,
            created_by_user_id=auth.user_id,
        )

        # Return JSON response for AJAX, redirect for form
        if "application/json" in content_type:
            return {"success": True, "credit_note_id": str(credit_note.invoice_id)}

        return RedirectResponse(
            url="/ar/credit-notes?success=Credit+note+created+successfully",
            status_code=303,
        )

    except Exception as e:
        if "application/json" in content_type:
            return JSONResponse(
                status_code=400,
                content={"detail": str(e)},
            )

        context = base_context(request, auth, "New Credit Note", "ar")
        context.update(ar_web_service.credit_note_form_context(db, str(auth.organization_id)))
        context["error"] = str(e)
        context["form_data"] = data
        return templates.TemplateResponse(request, "ifrs/ar/credit_note_form.html", context)


@router.get("/credit-notes/{credit_note_id}", response_class=HTMLResponse)
def view_credit_note(
    request: Request,
    credit_note_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """AR credit note detail page."""
    context = base_context(request, auth, "Credit Note Details", "ar")
    context.update(
        ar_web_service.credit_note_detail_context(
            db,
            str(auth.organization_id),
            credit_note_id,
        )
    )

    return templates.TemplateResponse(request, "ifrs/ar/credit_note_detail.html", context)


@router.post("/credit-notes/{credit_note_id}/delete")
def delete_credit_note(
    request: Request,
    credit_note_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Delete an AR credit note."""
    error = ar_web_service.delete_credit_note(db, str(auth.organization_id), credit_note_id)

    if error:
        context = base_context(request, auth, "Credit Note Details", "ar")
        context.update(
            ar_web_service.credit_note_detail_context(
                db, str(auth.organization_id), credit_note_id
            )
        )
        context["error"] = error
        return templates.TemplateResponse(request, "ifrs/ar/credit_note_detail.html", context)

    return RedirectResponse(url="/ar/credit-notes", status_code=303)


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
    """Upload an attachment for a customer invoice."""
    try:
        # Verify invoice exists and belongs to org
        invoice = ar_invoice_service.get(db, auth.organization_id, invoice_id)
        if not invoice or invoice.organization_id != auth.organization_id:
            return RedirectResponse(
                url=f"/ar/invoices/{invoice_id}?error=Invoice+not+found",
                status_code=303,
            )

        input_data = AttachmentInput(
            entity_type="CUSTOMER_INVOICE",
            entity_id=invoice_id,
            file_name=file.filename or "unnamed",
            content_type=file.content_type or "application/octet-stream",
            category=AttachmentCategory.INVOICE,
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
            url=f"/ar/invoices/{invoice_id}?success=Attachment+uploaded",
            status_code=303,
        )

    except ValueError as e:
        return RedirectResponse(
            url=f"/ar/invoices/{invoice_id}?error={str(e)}",
            status_code=303,
        )
    except Exception:
        return RedirectResponse(
            url=f"/ar/invoices/{invoice_id}?error=Upload+failed",
            status_code=303,
        )


@router.post("/receipts/{receipt_id}/attachments")
async def upload_receipt_attachment(
    receipt_id: str,
    file: UploadFile = File(...),
    description: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Upload an attachment for a customer receipt/payment."""
    from app.services.ifrs.ar.customer_payment import customer_payment_service

    try:
        # Verify receipt exists and belongs to org
        receipt = customer_payment_service.get(db, receipt_id)
        if not receipt or receipt.organization_id != auth.organization_id:
            return RedirectResponse(
                url=f"/ar/receipts/{receipt_id}?error=Receipt+not+found",
                status_code=303,
            )

        input_data = AttachmentInput(
            entity_type="CUSTOMER_PAYMENT",
            entity_id=receipt_id,
            file_name=file.filename or "unnamed",
            content_type=file.content_type or "application/octet-stream",
            category=AttachmentCategory.RECEIPT,
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
            url=f"/ar/receipts/{receipt_id}?success=Attachment+uploaded",
            status_code=303,
        )

    except ValueError as e:
        return RedirectResponse(
            url=f"/ar/receipts/{receipt_id}?error={str(e)}",
            status_code=303,
        )
    except Exception:
        return RedirectResponse(
            url=f"/ar/receipts/{receipt_id}?error=Upload+failed",
            status_code=303,
        )


@router.post("/credit-notes/{credit_note_id}/attachments")
async def upload_credit_note_attachment(
    credit_note_id: str,
    file: UploadFile = File(...),
    description: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Upload an attachment for a credit note."""
    try:
        # Verify credit note exists and belongs to org
        credit_note = ar_invoice_service.get(db, auth.organization_id, credit_note_id)
        if not credit_note or credit_note.organization_id != auth.organization_id:
            return RedirectResponse(
                url=f"/ar/credit-notes/{credit_note_id}?error=Credit+note+not+found",
                status_code=303,
            )

        input_data = AttachmentInput(
            entity_type="CREDIT_NOTE",
            entity_id=credit_note_id,
            file_name=file.filename or "unnamed",
            content_type=file.content_type or "application/octet-stream",
            category=AttachmentCategory.CREDIT_NOTE,
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
            url=f"/ar/credit-notes/{credit_note_id}?success=Attachment+uploaded",
            status_code=303,
        )

    except ValueError as e:
        return RedirectResponse(
            url=f"/ar/credit-notes/{credit_note_id}?error={str(e)}",
            status_code=303,
        )
    except Exception:
        return RedirectResponse(
            url=f"/ar/credit-notes/{credit_note_id}?error=Upload+failed",
            status_code=303,
        )


@router.post("/customers/{customer_id}/attachments")
async def upload_customer_attachment(
    customer_id: str,
    file: UploadFile = File(...),
    description: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Upload an attachment for a customer."""
    try:
        # Verify customer exists and belongs to org
        customer = customer_service.get(db, auth.organization_id, customer_id)
        if not customer or customer.organization_id != auth.organization_id:
            return RedirectResponse(
                url=f"/ar/customers/{customer_id}?error=Customer+not+found",
                status_code=303,
            )

        input_data = AttachmentInput(
            entity_type="CUSTOMER",
            entity_id=customer_id,
            file_name=file.filename or "unnamed",
            content_type=file.content_type or "application/octet-stream",
            category=AttachmentCategory.CUSTOMER,
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
            url=f"/ar/customers/{customer_id}?success=Attachment+uploaded",
            status_code=303,
        )

    except ValueError as e:
        return RedirectResponse(
            url=f"/ar/customers/{customer_id}?error={str(e)}",
            status_code=303,
        )
    except Exception:
        return RedirectResponse(
            url=f"/ar/customers/{customer_id}?error=Upload+failed",
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
        return RedirectResponse(url="/ar/invoices?error=Attachment+not+found", status_code=303)

    file_path = attachment_service.get_file_path(attachment)

    if not file_path.exists():
        return RedirectResponse(url="/ar/invoices?error=File+not+found", status_code=303)

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
        return RedirectResponse(url="/ar/invoices?error=Attachment+not+found", status_code=303)

    # Get entity info for redirect
    entity_type = attachment.entity_type
    entity_id = attachment.entity_id

    # Delete the attachment
    attachment_service.delete(db, attachment_id, auth.organization_id)

    # Redirect based on entity type
    redirect_map = {
        "CUSTOMER_INVOICE": f"/ar/invoices/{entity_id}",
        "CUSTOMER_PAYMENT": f"/ar/receipts/{entity_id}",
        "CREDIT_NOTE": f"/ar/credit-notes/{entity_id}",
        "CUSTOMER": f"/ar/customers/{entity_id}",
    }

    redirect_url = redirect_map.get(entity_type, "/ar/invoices")
    return RedirectResponse(
        url=f"{redirect_url}?success=Attachment+deleted",
        status_code=303,
    )
