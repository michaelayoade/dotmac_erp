"""
AR (Accounts Receivable) Web Routes.

HTML template routes for Customers, Invoices, and Receipts.
"""

from typing import Optional

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.finance.ar.web import ar_web_service
from app.web.deps import get_db, require_finance_access, WebAuthContext


router = APIRouter(prefix="/ar", tags=["ar-web"])


@router.get("/customers", response_class=HTMLResponse)
def list_customers(
    request: Request,
    search: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Customers list page."""
    return ar_web_service.list_customers_response(request, auth, db, search, status, page)


@router.get("/customers/new", response_class=HTMLResponse)
def new_customer_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New customer form page."""
    return ar_web_service.customer_new_form_response(request, auth, db)


@router.get("/customers/{customer_id}", response_class=HTMLResponse)
def view_customer(
    request: Request,
    customer_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Customer detail page."""
    return ar_web_service.customer_detail_response(request, auth, db, customer_id)


@router.get("/customers/{customer_id}/edit", response_class=HTMLResponse)
def edit_customer_form(
    request: Request,
    customer_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Edit customer form page."""
    return ar_web_service.customer_edit_form_response(request, auth, db, customer_id)


@router.post("/customers/new")
async def create_customer(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Handle customer form submission."""
    return await ar_web_service.create_customer_response(request, auth, db)


@router.post("/customers/{customer_id}/edit")
async def update_customer(
    request: Request,
    customer_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Handle customer update form submission."""
    return await ar_web_service.update_customer_response(request, auth, db, customer_id)


@router.post("/customers/{customer_id}/delete")
def delete_customer(
    request: Request,
    customer_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
):
    """Bulk delete customers."""
    return await ar_web_service.bulk_delete_customers_response(request, auth, db)


@router.post("/customers/bulk-export")
async def bulk_export_customers(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Export selected customers to CSV."""
    return await ar_web_service.bulk_export_customers_response(request, auth, db)


@router.post("/customers/bulk-activate")
async def bulk_activate_customers(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Bulk activate customers."""
    return await ar_web_service.bulk_activate_customers_response(request, auth, db)


@router.post("/customers/bulk-deactivate")
async def bulk_deactivate_customers(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Bulk deactivate customers."""
    return await ar_web_service.bulk_deactivate_customers_response(request, auth, db)


@router.get("/invoices", response_class=HTMLResponse)
def list_invoices(
    request: Request,
    search: Optional[str] = None,
    customer_id: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """AR invoices list page."""
    return ar_web_service.list_invoices_response(
        request,
        auth,
        db,
        search,
        customer_id,
        status,
        start_date,
        end_date,
        page,
    )


@router.get("/invoices/new", response_class=HTMLResponse)
def new_invoice_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New AR invoice form page."""
    return ar_web_service.invoice_new_form_response(request, auth, db)


@router.post("/invoices/new")
async def create_invoice(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Handle AR invoice form submission."""
    return await ar_web_service.create_invoice_response(request, auth, db)


@router.get("/invoices/{invoice_id}", response_class=HTMLResponse)
def view_invoice(
    request: Request,
    invoice_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """AR invoice detail page."""
    return ar_web_service.invoice_detail_response(request, auth, db, invoice_id)


@router.post("/invoices/{invoice_id}/delete")
def delete_invoice(
    request: Request,
    invoice_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
):
    """Bulk delete AR invoices (only DRAFT status)."""
    return await ar_web_service.bulk_delete_invoices_response(request, auth, db)


@router.post("/invoices/bulk-export")
async def bulk_export_invoices(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Export selected AR invoices to CSV."""
    return await ar_web_service.bulk_export_invoices_response(request, auth, db)


@router.get("/receipts", response_class=HTMLResponse)
def list_receipts(
    request: Request,
    search: Optional[str] = None,
    customer_id: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """AR receipts list page."""
    return ar_web_service.list_receipts_response(
        request,
        auth,
        db,
        search,
        customer_id,
        status,
        start_date,
        end_date,
        page,
    )


@router.get("/receipts/new", response_class=HTMLResponse)
def new_receipt_form(
    request: Request,
    invoice_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New AR receipt form page."""
    return ar_web_service.receipt_new_form_response(request, auth, db, invoice_id)


@router.get("/receipts/{receipt_id}", response_class=HTMLResponse)
def view_receipt(
    request: Request,
    receipt_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """AR receipt detail page."""
    return ar_web_service.receipt_detail_response(request, auth, db, receipt_id)


@router.post("/receipts/new")
async def create_receipt(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Handle AR receipt form submission."""
    return await ar_web_service.create_receipt_response(request, auth, db)


@router.get("/receipts/{receipt_id}/edit", response_class=HTMLResponse)
def edit_receipt_form(
    request: Request,
    receipt_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Edit AR receipt form page."""
    return ar_web_service.receipt_edit_form_response(request, auth, db, receipt_id)


@router.post("/receipts/{receipt_id}/edit")
async def update_receipt(
    request: Request,
    receipt_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Handle AR receipt update form submission."""
    return await ar_web_service.update_receipt_response(request, auth, db, receipt_id)


@router.post("/receipts/{receipt_id}/delete")
def delete_receipt(
    request: Request,
    receipt_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
):
    """Bulk delete AR receipts (only PENDING status)."""
    return await ar_web_service.bulk_delete_receipts_response(request, auth, db)


@router.post("/receipts/bulk-export")
async def bulk_export_receipts(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Export selected AR receipts to CSV."""
    return await ar_web_service.bulk_export_receipts_response(request, auth, db)


@router.get("/credit-notes", response_class=HTMLResponse)
def list_credit_notes(
    request: Request,
    search: Optional[str] = None,
    customer_id: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """AR credit notes list page."""
    return ar_web_service.list_credit_notes_response(
        request,
        auth,
        db,
        search,
        customer_id,
        status,
        start_date,
        end_date,
        page,
    )


@router.get("/credit-notes/new", response_class=HTMLResponse)
def new_credit_note_form(
    request: Request,
    invoice_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New AR credit note form page."""
    return ar_web_service.credit_note_new_form_response(request, auth, db, invoice_id)


@router.post("/credit-notes/new")
async def create_credit_note(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Handle AR credit note form submission."""
    return await ar_web_service.create_credit_note_response(request, auth, db)


@router.get("/credit-notes/{credit_note_id}", response_class=HTMLResponse)
def view_credit_note(
    request: Request,
    credit_note_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """AR credit note detail page."""
    return ar_web_service.credit_note_detail_response(request, auth, db, credit_note_id)


@router.post("/credit-notes/{credit_note_id}/delete")
def delete_credit_note(
    request: Request,
    credit_note_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Delete an AR credit note."""
    return ar_web_service.delete_credit_note_response(request, auth, db, credit_note_id)


@router.get("/aging", response_class=HTMLResponse)
def aging_report(
    request: Request,
    as_of_date: Optional[str] = None,
    customer_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """AR aging report page."""
    return ar_web_service.aging_report_response(request, auth, db, as_of_date, customer_id)


@router.post("/invoices/{invoice_id}/attachments")
async def upload_invoice_attachment(
    invoice_id: str,
    file: UploadFile = File(...),
    description: Optional[str] = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
    description: Optional[str] = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
    description: Optional[str] = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
    description: Optional[str] = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
):
    """Download an attachment file."""
    return ar_web_service.download_attachment_response(attachment_id, auth, db)


@router.post("/attachments/{attachment_id}/delete")
def delete_attachment(
    attachment_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Delete an attachment."""
    return ar_web_service.delete_attachment_response(attachment_id, auth, db)
