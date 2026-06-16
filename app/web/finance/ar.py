"""
AR (Accounts Receivable) Web Routes.

HTML template routes for Customers, Invoices, and Receipts.
"""

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from sqlalchemy.orm import Session

from app.services.finance.ar.web import ar_web_service
from app.services.finance.rpt.async_exports import (
    get_completed_export_for_download,
    get_export_status,
    queue_background_export,
)
from app.web.deps import get_db_for_org, WebAuthContext, require_finance_access

router = APIRouter(prefix="/ar", tags=["ar-web"])


@router.get("/", response_class=HTMLResponse)
def ar_home(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Accounts receivable landing page."""
    return ar_web_service.ar_home_response(request, auth, db)


@router.get("/customers", response_class=HTMLResponse)
def list_customers(
    request: Request,
    search: str | None = None,
    status: str | None = None,
    parent_customer_id: str | None = None,
    show_subs: bool = False,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=25, le=200),
    sort: str | None = None,
    sort_dir: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Customers list page."""
    return ar_web_service.list_customers_response(
        request=request,
        auth=auth,
        db=db,
        search=search,
        status=status,
        page=page,
        sort=sort,
        sort_dir=sort_dir,
        limit=limit,
        parent_customer_id=parent_customer_id,
        show_subs=show_subs,
    )


@router.get("/customers/new", response_class=HTMLResponse)
def new_customer_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """New customer form page."""
    return ar_web_service.customer_new_form_response(request, auth, db)


@router.get("/customers/export")
async def export_all_customers(
    request: Request,
    search: str = "",
    status: str = "",
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Export all customers matching filters to CSV."""
    return await ar_web_service.export_all_customers_response(auth, db, search, status)


@router.get("/customers/{customer_id}", response_class=HTMLResponse)
def view_customer(
    request: Request,
    customer_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Customer detail page."""
    return ar_web_service.customer_detail_response(request, auth, db, customer_id)


@router.get("/customers/{customer_id}/statement", response_class=HTMLResponse)
def customer_statement(
    request: Request,
    customer_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Consolidated statement of account."""
    return ar_web_service.customer_statement_response(request, auth, db, customer_id)


@router.get("/customers/{customer_id}/statement/pdf")
def customer_statement_pdf(
    request: Request,
    customer_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Download the statement of account as a PDF."""
    return ar_web_service.customer_statement_pdf_response(
        request, auth, db, customer_id
    )


@router.get(
    "/customers/{customer_id}/consolidated-payment", response_class=HTMLResponse
)
def consolidated_payment_form(
    request: Request,
    customer_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Form to record a consolidated reseller payment across the family."""
    return ar_web_service.consolidated_payment_form_response(
        request, auth, db, customer_id
    )


@router.post("/customers/{customer_id}/consolidated-payment")
async def create_consolidated_payment(
    request: Request,
    customer_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Record a consolidated reseller payment (FIFO across the family)."""
    return await ar_web_service.create_consolidated_payment_response(
        request, auth, db, customer_id
    )


@router.get("/customers/{customer_id}/edit", response_class=HTMLResponse)
def edit_customer_form(
    request: Request,
    customer_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit customer form page."""
    return ar_web_service.customer_edit_form_response(request, auth, db, customer_id)


@router.post("/customers/new")
async def create_customer(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Handle customer form submission."""
    return await ar_web_service.create_customer_response(request, auth, db)


@router.post("/customers/{customer_id}/edit")
async def update_customer(
    request: Request,
    customer_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Handle customer update form submission."""
    return await ar_web_service.update_customer_response(request, auth, db, customer_id)


@router.post("/customers/{customer_id}/delete")
def delete_customer(
    request: Request,
    customer_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Delete a customer."""
    return ar_web_service.delete_customer_response(request, auth, db, customer_id)


# ═══════════════════════════════════════════════════════════════════
# Bulk Actions - Customers
# ═══════════════════════════════════════════════════════════════════


@router.post("/customers/bulk-delete")
async def bulk_delete_customers(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Bulk delete customers."""
    return await ar_web_service.bulk_delete_customers_response(request, auth, db)


@router.post("/customers/bulk-export")
async def bulk_export_customers(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Export selected customers to CSV."""
    return await ar_web_service.bulk_export_customers_response(request, auth, db)


@router.post("/customers/bulk-activate")
async def bulk_activate_customers(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Bulk activate customers."""
    return await ar_web_service.bulk_activate_customers_response(request, auth, db)


@router.post("/customers/bulk-deactivate")
async def bulk_deactivate_customers(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Bulk deactivate customers."""
    return await ar_web_service.bulk_deactivate_customers_response(request, auth, db)


@router.get("/invoices", response_class=HTMLResponse)
def list_invoices(
    request: Request,
    search: str | None = None,
    customer_id: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=10, le=500),
    sort: str | None = None,
    sort_dir: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """AR invoices list page."""
    return ar_web_service.list_invoices_response(
        request=request,
        auth=auth,
        db=db,
        search=search,
        customer_id=customer_id,
        status=status,
        start_date=start_date,
        end_date=end_date,
        page=page,
        limit=limit,
        sort=sort,
        sort_dir=sort_dir,
    )


@router.get("/invoices/new", response_class=HTMLResponse)
def new_invoice_form(
    request: Request,
    customer_id: str | None = Query(None),
    duplicate_from: str | None = Query(None),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """New AR invoice form page."""
    return ar_web_service.invoice_new_form_response(
        request, auth, db, customer_id=customer_id, duplicate_from=duplicate_from
    )


@router.post("/invoices/new")
async def create_invoice(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Handle AR invoice form submission."""
    return await ar_web_service.create_invoice_response(request, auth, db)


@router.get("/invoices/export")
async def export_all_invoices(
    request: Request,
    search: str = "",
    status: str = "",
    customer_id: str = "",
    start_date: str = "",
    end_date: str = "",
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Export all invoices matching filters to CSV."""
    return await ar_web_service.export_all_invoices_response(
        auth, db, search, status, start_date, end_date, customer_id
    )


@router.post("/invoices/export")
def queue_invoices_export(
    search: str = "",
    status: str = "",
    customer_id: str = "",
    start_date: str = "",
    end_date: str = "",
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> JSONResponse:
    """Queue all AR invoices matching filters for CSV export."""
    instance = queue_background_export(
        db,
        auth.organization_id,
        auth.user_id,
        report_code="AR_INVOICES",
        parameters={
            "search": search,
            "status": status,
            "customer_id": customer_id,
            "start_date": start_date,
            "end_date": end_date,
        },
        output_format="CSV",
    )
    return JSONResponse(
        {
            "message": "AR Invoices export is processing. You will be notified when it is ready.",
            "instance_id": str(instance.instance_id),
            "status_url": f"/finance/ar/invoices/exports/{instance.instance_id}/status",
        },
        status_code=202,
    )


@router.get("/invoices/exports/{instance_id}/download")
def download_invoices_export(
    instance_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> Response:
    """Download a completed queued AR Invoices export."""
    body, filename, media_type, content_length = get_completed_export_for_download(
        db,
        auth.organization_id,
        auth.user_id,
        instance_id,
        report_code="AR_INVOICES",
    )
    if hasattr(body, "__fspath__"):
        return FileResponse(body, filename=filename, media_type=media_type)

    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    return StreamingResponse(body, media_type=media_type, headers=headers)


@router.get("/invoices/exports/{instance_id}/status")
def invoices_export_status(
    instance_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> JSONResponse:
    """Return the status of a queued AR Invoices export."""
    return JSONResponse(
        get_export_status(
            db,
            auth.organization_id,
            auth.user_id,
            instance_id,
            report_code="AR_INVOICES",
        )
    )


@router.get("/invoices/{invoice_id}", response_class=HTMLResponse)
def view_invoice(
    request: Request,
    invoice_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """AR invoice detail page."""
    return ar_web_service.invoice_detail_response(request, auth, db, invoice_id)


@router.get("/invoices/{invoice_id}/edit", response_class=HTMLResponse)
def edit_invoice_form(
    request: Request,
    invoice_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit AR invoice form page."""
    return ar_web_service.invoice_edit_form_response(request, auth, db, invoice_id)


@router.post("/invoices/{invoice_id}/edit")
async def update_invoice(
    request: Request,
    invoice_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Handle AR invoice update form submission."""
    return await ar_web_service.update_invoice_response(request, auth, db, invoice_id)


@router.post("/invoices/{invoice_id}/submit")
def submit_invoice(
    request: Request,
    invoice_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Submit AR invoice for approval."""
    return ar_web_service.submit_invoice_response(request, auth, db, invoice_id)


@router.post("/invoices/{invoice_id}/approve")
def approve_invoice(
    request: Request,
    invoice_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Approve AR invoice."""
    return ar_web_service.approve_invoice_response(request, auth, db, invoice_id)


@router.post("/invoices/{invoice_id}/post")
def post_invoice(
    request: Request,
    invoice_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Post AR invoice to general ledger."""
    return ar_web_service.post_invoice_response(request, auth, db, invoice_id)


@router.post("/invoices/{invoice_id}/void")
def void_invoice(
    request: Request,
    invoice_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Void an AR invoice."""
    return ar_web_service.void_invoice_response(request, auth, db, invoice_id)


@router.post("/invoices/{invoice_id}/cancel")
def cancel_invoice(
    request: Request,
    invoice_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Cancel an AR invoice, returning to DRAFT for editing."""
    return ar_web_service.cancel_invoice_response(request, auth, db, invoice_id)


@router.post("/invoices/{invoice_id}/delete")
def delete_invoice(
    request: Request,
    invoice_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Delete an AR invoice."""
    return ar_web_service.delete_invoice_response(request, auth, db, invoice_id)


# ═══════════════════════════════════════════════════════════════════
# Bulk Actions - Invoices
# ═══════════════════════════════════════════════════════════════════


@router.post("/invoices/bulk-delete")
async def bulk_delete_invoices(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Bulk delete AR invoices (only DRAFT status)."""
    return await ar_web_service.bulk_delete_invoices_response(request, auth, db)


@router.post("/invoices/bulk-export")
async def bulk_export_invoices(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Export selected AR invoices to CSV."""
    return await ar_web_service.bulk_export_invoices_response(request, auth, db)


@router.post("/invoices/bulk-approve")
async def bulk_approve_invoices(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Bulk approve AR invoices (from SUBMITTED status)."""
    return await ar_web_service.bulk_approve_invoices_response(request, auth, db)


@router.post("/invoices/bulk-post")
async def bulk_post_invoices(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Bulk post AR invoices to General Ledger (from APPROVED status)."""
    return await ar_web_service.bulk_post_invoices_response(request, auth, db)


@router.get("/receipts", response_class=HTMLResponse)
def list_receipts(
    request: Request,
    search: str | None = None,
    customer_id: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=10, le=500),
    sort: str | None = None,
    sort_dir: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """AR receipts list page."""
    return ar_web_service.list_receipts_response(
        request=request,
        auth=auth,
        db=db,
        search=search,
        customer_id=customer_id,
        status=status,
        start_date=start_date,
        end_date=end_date,
        page=page,
        limit=limit,
        sort=sort,
        sort_dir=sort_dir,
    )


@router.get("/receipts/new", response_class=HTMLResponse)
def new_receipt_form(
    request: Request,
    invoice_id: str | None = None,
    customer_id: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """New AR receipt form page."""
    return ar_web_service.receipt_new_form_response(
        request,
        auth,
        db,
        invoice_id=invoice_id,
        customer_id=customer_id,
    )


@router.get("/receipts/export")
async def export_all_receipts(
    request: Request,
    search: str = "",
    status: str = "",
    customer_id: str = "",
    start_date: str = "",
    end_date: str = "",
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Export all receipts matching filters to CSV."""
    return await ar_web_service.export_all_receipts_response(
        auth, db, search, status, start_date, end_date, customer_id
    )


@router.post("/receipts/export")
def queue_receipts_export(
    search: str = "",
    status: str = "",
    customer_id: str = "",
    start_date: str = "",
    end_date: str = "",
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> JSONResponse:
    """Queue all AR receipts matching filters for CSV export."""
    instance = queue_background_export(
        db,
        auth.organization_id,
        auth.user_id,
        report_code="AR_RECEIPTS",
        parameters={
            "search": search,
            "status": status,
            "customer_id": customer_id,
            "start_date": start_date,
            "end_date": end_date,
        },
        output_format="CSV",
    )
    return JSONResponse(
        {
            "message": "AR Receipts export is processing. You will be notified when it is ready.",
            "instance_id": str(instance.instance_id),
            "status_url": f"/finance/ar/receipts/exports/{instance.instance_id}/status",
        },
        status_code=202,
    )


@router.get("/receipts/exports/{instance_id}/download")
def download_receipts_export(
    instance_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> Response:
    """Download a completed queued AR Receipts export."""
    body, filename, media_type, content_length = get_completed_export_for_download(
        db,
        auth.organization_id,
        auth.user_id,
        instance_id,
        report_code="AR_RECEIPTS",
    )
    if hasattr(body, "__fspath__"):
        return FileResponse(body, filename=filename, media_type=media_type)

    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    return StreamingResponse(body, media_type=media_type, headers=headers)


@router.get("/receipts/exports/{instance_id}/status")
def receipts_export_status(
    instance_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
) -> JSONResponse:
    """Return the status of a queued AR Receipts export."""
    return JSONResponse(
        get_export_status(
            db,
            auth.organization_id,
            auth.user_id,
            instance_id,
            report_code="AR_RECEIPTS",
        )
    )


@router.get("/receipts/{receipt_id}", response_class=HTMLResponse)
def view_receipt(
    request: Request,
    receipt_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """AR receipt detail page."""
    return ar_web_service.receipt_detail_response(request, auth, db, receipt_id)


@router.post("/receipts/new")
async def create_receipt(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Handle AR receipt form submission."""
    return await ar_web_service.create_receipt_response(request, auth, db)


@router.get("/receipts/{receipt_id}/edit", response_class=HTMLResponse)
def edit_receipt_form(
    request: Request,
    receipt_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit AR receipt form page."""
    return ar_web_service.receipt_edit_form_response(request, auth, db, receipt_id)


@router.post("/receipts/{receipt_id}/edit")
async def update_receipt(
    request: Request,
    receipt_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Handle AR receipt update form submission."""
    return await ar_web_service.update_receipt_response(request, auth, db, receipt_id)


@router.post("/receipts/{receipt_id}/delete")
def delete_receipt(
    request: Request,
    receipt_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Delete an AR receipt."""
    return ar_web_service.delete_receipt_response(request, auth, db, receipt_id)


# ═══════════════════════════════════════════════════════════════════
# Bulk Actions - Receipts
# ═══════════════════════════════════════════════════════════════════


@router.post("/receipts/bulk-delete")
async def bulk_delete_receipts(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Bulk delete AR receipts (only PENDING status)."""
    return await ar_web_service.bulk_delete_receipts_response(request, auth, db)


@router.post("/receipts/bulk-export")
async def bulk_export_receipts(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Export selected AR receipts to CSV."""
    return await ar_web_service.bulk_export_receipts_response(request, auth, db)


@router.get("/credit-notes", response_class=HTMLResponse)
def list_credit_notes(
    request: Request,
    search: str | None = None,
    customer_id: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=10, le=500),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """AR credit notes list page."""
    return ar_web_service.list_credit_notes_response(
        request=request,
        auth=auth,
        db=db,
        search=search,
        customer_id=customer_id,
        status=status,
        start_date=start_date,
        end_date=end_date,
        page=page,
        limit=limit,
    )


@router.get("/credit-notes/new", response_class=HTMLResponse)
def new_credit_note_form(
    request: Request,
    invoice_id: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """New AR credit note form page."""
    return ar_web_service.credit_note_new_form_response(request, auth, db, invoice_id)


@router.post("/credit-notes/new")
async def create_credit_note(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Handle AR credit note form submission."""
    return await ar_web_service.create_credit_note_response(request, auth, db)


@router.get("/credit-notes/{credit_note_id}", response_class=HTMLResponse)
def view_credit_note(
    request: Request,
    credit_note_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """AR credit note detail page."""
    return ar_web_service.credit_note_detail_response(request, auth, db, credit_note_id)


@router.get("/credit-notes/{credit_note_id}/edit", response_class=HTMLResponse)
def edit_credit_note_form(
    request: Request,
    credit_note_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit AR credit note form page."""
    return ar_web_service.credit_note_edit_form_response(
        request, auth, db, credit_note_id
    )


@router.post("/credit-notes/{credit_note_id}/edit")
async def update_credit_note(
    request: Request,
    credit_note_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Handle AR credit note update form submission."""
    return await ar_web_service.update_credit_note_response(
        request, auth, db, credit_note_id
    )


@router.post("/credit-notes/{credit_note_id}/submit")
def submit_credit_note(
    request: Request,
    credit_note_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Submit AR credit note for approval."""
    return ar_web_service.submit_credit_note_response(request, auth, db, credit_note_id)


@router.post("/credit-notes/{credit_note_id}/approve")
def approve_credit_note(
    request: Request,
    credit_note_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Approve AR credit note."""
    return ar_web_service.approve_credit_note_response(
        request, auth, db, credit_note_id
    )


@router.post("/credit-notes/{credit_note_id}/post")
def post_credit_note(
    request: Request,
    credit_note_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Post AR credit note to general ledger."""
    return ar_web_service.post_credit_note_response(request, auth, db, credit_note_id)


@router.post("/credit-notes/{credit_note_id}/void")
def void_credit_note(
    request: Request,
    credit_note_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Void an AR credit note."""
    return ar_web_service.void_credit_note_response(request, auth, db, credit_note_id)


@router.post("/credit-notes/{credit_note_id}/delete")
def delete_credit_note(
    request: Request,
    credit_note_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Delete an AR credit note."""
    return ar_web_service.delete_credit_note_response(request, auth, db, credit_note_id)


@router.get("/aging", response_class=HTMLResponse)
def aging_report(
    request: Request,
    as_of_date: str | None = None,
    customer_id: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """AR aging report page."""
    return ar_web_service.aging_report_response(
        request, auth, db, as_of_date, customer_id
    )


@router.post("/invoices/{invoice_id}/attachments")
async def upload_invoice_attachment(
    invoice_id: str,
    file: UploadFile = File(...),
    description: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Upload an attachment for a customer invoice."""
    return await ar_web_service.upload_invoice_attachment_response(
        invoice_id,
        file,
        description,
        auth,
        db,
    )


@router.post("/receipts/{receipt_id}/attachments")
async def upload_receipt_attachment(
    receipt_id: str,
    file: UploadFile = File(...),
    description: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Upload an attachment for a customer receipt/payment."""
    return await ar_web_service.upload_receipt_attachment_response(
        receipt_id,
        file,
        description,
        auth,
        db,
    )


@router.post("/credit-notes/{credit_note_id}/attachments")
async def upload_credit_note_attachment(
    credit_note_id: str,
    file: UploadFile = File(...),
    description: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Upload an attachment for a credit note."""
    return await ar_web_service.upload_credit_note_attachment_response(
        credit_note_id,
        file,
        description,
        auth,
        db,
    )


@router.post("/customers/{customer_id}/attachments")
async def upload_customer_attachment(
    customer_id: str,
    file: UploadFile = File(...),
    description: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Upload an attachment for a customer."""
    return await ar_web_service.upload_customer_attachment_response(
        customer_id,
        file,
        description,
        auth,
        db,
    )


@router.get("/attachments/{attachment_id}/download")
def download_attachment(
    attachment_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Download an attachment file."""
    return ar_web_service.download_attachment_response(attachment_id, auth, db)


@router.post("/attachments/{attachment_id}/delete")
def delete_attachment(
    attachment_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db_for_org),
):
    """Delete an attachment."""
    return ar_web_service.delete_attachment_response(attachment_id, auth, db)
