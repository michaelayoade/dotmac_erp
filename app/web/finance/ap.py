"""
AP (Accounts Payable) Web Routes.

HTML template routes for Suppliers, Invoices, and Payments.
"""

import logging

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.services.finance.ap.web import ap_web_service
from app.web.deps import (
    WebAuthContext,
    get_db,
    require_finance_access,
    require_web_permission,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ap", tags=["ap-web"])


@router.get("/suppliers", response_class=HTMLResponse)
def list_suppliers(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    search: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=10, le=500),
    sort: str | None = None,
    sort_dir: str | None = None,
    db: Session = Depends(get_db),
):
    """Suppliers list page."""
    return ap_web_service.list_suppliers_response(
        request,
        auth,
        db,
        search,
        status,
        page,
        limit,
        sort,
        sort_dir,
    )


@router.get("/suppliers/search")
def supplier_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=8, ge=1, le=20),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Search active suppliers for typeahead/autocomplete."""
    payload = ap_web_service.supplier_typeahead(
        db=db,
        organization_id=str(auth.organization_id),
        query=q,
        limit=limit,
    )
    return JSONResponse(payload)


@router.get("/people/search")
def people_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=25, ge=1, le=100),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Search people by name/email for comment @mentions."""
    org_id = auth.organization_id
    if org_id is None:
        return JSONResponse({"items": []}, status_code=401)

    payload = ap_web_service.people_search(
        db=db,
        organization_id=str(org_id),
        query=q,
        limit=limit,
    )
    return JSONResponse(payload)


@router.get("/suppliers/new", response_class=HTMLResponse)
def new_supplier_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New supplier form page."""
    return ap_web_service.supplier_new_form_response(request, auth, db)


@router.get("/suppliers/export")
async def export_all_suppliers(
    request: Request,
    search: str = "",
    status: str = "",
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Export all suppliers matching filters to CSV."""
    return await ap_web_service.export_all_suppliers_response(auth, db, search, status)


@router.get("/suppliers/{supplier_id}", response_class=HTMLResponse)
def view_supplier(
    request: Request,
    supplier_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Supplier detail page."""
    return ap_web_service.supplier_detail_response(request, auth, db, supplier_id)


@router.get("/suppliers/{supplier_id}/edit", response_class=HTMLResponse)
def edit_supplier_form(
    request: Request,
    supplier_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Edit supplier form page."""
    return ap_web_service.supplier_edit_form_response(request, auth, db, supplier_id)


@router.post("/suppliers/new")
async def create_supplier(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Handle supplier form submission."""
    return await ap_web_service.create_supplier_response(request, auth, db)


@router.post("/suppliers/{supplier_id}/edit")
async def update_supplier(
    request: Request,
    supplier_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Handle supplier update form submission."""
    return await ap_web_service.update_supplier_response(request, auth, db, supplier_id)


@router.post("/suppliers/{supplier_id}/delete")
def delete_supplier(
    request: Request,
    supplier_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Delete a supplier."""
    return ap_web_service.delete_supplier_response(request, auth, db, supplier_id)


# ═══════════════════════════════════════════════════════════════════
# Bulk Actions - Suppliers
# ═══════════════════════════════════════════════════════════════════


@router.post("/suppliers/bulk-delete")
async def bulk_delete_suppliers(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Bulk delete suppliers."""
    return await ap_web_service.bulk_delete_suppliers_response(request, auth, db)


@router.post("/suppliers/bulk-export")
async def bulk_export_suppliers(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Export selected suppliers to CSV."""
    return await ap_web_service.bulk_export_suppliers_response(request, auth, db)


@router.post("/suppliers/bulk-activate")
async def bulk_activate_suppliers(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Bulk activate suppliers."""
    return await ap_web_service.bulk_activate_suppliers_response(request, auth, db)


@router.post("/suppliers/bulk-deactivate")
async def bulk_deactivate_suppliers(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Bulk deactivate suppliers."""
    return await ap_web_service.bulk_deactivate_suppliers_response(request, auth, db)


@router.get("/invoices", response_class=HTMLResponse)
def list_invoices(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    search: str | None = None,
    supplier_id: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = Query(default=1, ge=1),
    sort: str | None = None,
    sort_dir: str | None = None,
    db: Session = Depends(get_db),
):
    """AP invoices list page."""
    return ap_web_service.list_invoices_response(
        request,
        auth,
        search,
        supplier_id,
        status,
        start_date,
        end_date,
        page,
        db,
        sort,
        sort_dir,
    )


@router.get("/invoices/new", response_class=HTMLResponse)
def new_invoice_form(
    request: Request,
    supplier_id: str | None = None,
    po_id: str | None = None,
    duplicate_from: str | None = Query(None),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New AP invoice form page."""
    return ap_web_service.invoice_new_form_response(
        request, auth, supplier_id, po_id, db, duplicate_from=duplicate_from
    )


@router.post("/invoices/new")
async def create_invoice(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Handle AP invoice form submission."""
    return await ap_web_service.create_invoice_response(request, auth, db)


@router.get("/invoices/export")
async def export_all_ap_invoices(
    request: Request,
    search: str = "",
    status: str = "",
    supplier_id: str = "",
    start_date: str = "",
    end_date: str = "",
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Export all AP invoices matching filters to CSV."""
    return await ap_web_service.export_all_invoices_response(
        auth, db, search, status, start_date, end_date, supplier_id
    )


@router.get("/invoices/{invoice_id}", response_class=HTMLResponse)
def view_invoice(
    request: Request,
    invoice_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """AP invoice detail page."""
    return ap_web_service.invoice_detail_response(request, auth, db, invoice_id)


@router.get("/invoices/{invoice_id}/edit", response_class=HTMLResponse)
def edit_invoice_form(
    request: Request,
    invoice_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Edit AP invoice form page."""
    return ap_web_service.invoice_edit_form_response(request, auth, db, invoice_id)


@router.post("/invoices/{invoice_id}/edit")
async def update_invoice(
    request: Request,
    invoice_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Handle AP invoice update form submission."""
    return await ap_web_service.update_invoice_response(request, auth, db, invoice_id)


@router.post("/invoices/{invoice_id}/submit")
def submit_invoice(
    request: Request,
    invoice_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Submit AP invoice for approval."""
    return ap_web_service.submit_invoice_response(request, auth, db, invoice_id)


@router.post("/invoices/{invoice_id}/approve")
def approve_invoice(
    request: Request,
    invoice_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Approve AP invoice."""
    return ap_web_service.approve_invoice_response(request, auth, db, invoice_id)


@router.post("/invoices/{invoice_id}/post")
def post_invoice(
    request: Request,
    invoice_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Post AP invoice to general ledger."""
    return ap_web_service.post_invoice_response(request, auth, db, invoice_id)


@router.post("/invoices/{invoice_id}/void")
def void_invoice(
    request: Request,
    invoice_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Void an AP invoice."""
    return ap_web_service.void_invoice_response(request, auth, db, invoice_id)


@router.post("/invoices/{invoice_id}/comments")
async def add_invoice_comment(
    request: Request,
    invoice_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Append an internal comment to an AP invoice."""
    return await ap_web_service.add_invoice_comment_response(
        request, auth, db, invoice_id
    )


@router.post("/invoices/{invoice_id}/delete")
def delete_invoice(
    request: Request,
    invoice_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Delete an AP invoice."""
    return ap_web_service.delete_invoice_response(request, auth, db, invoice_id)


# ═══════════════════════════════════════════════════════════════════
# Bulk Actions - Invoices
# ═══════════════════════════════════════════════════════════════════


@router.post("/invoices/bulk-delete")
async def bulk_delete_invoices(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Bulk delete AP invoices (only DRAFT status)."""
    return await ap_web_service.bulk_delete_invoices_response(request, auth, db)


@router.post("/invoices/bulk-export")
async def bulk_export_invoices(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Export selected AP invoices to CSV."""
    return await ap_web_service.bulk_export_invoices_response(request, auth, db)


@router.post("/invoices/bulk-approve")
async def bulk_approve_invoices(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Bulk approve AP invoices (from SUBMITTED status)."""
    return await ap_web_service.bulk_approve_invoices_response(request, auth, db)


@router.post("/invoices/bulk-post")
async def bulk_post_invoices(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Bulk post AP invoices to General Ledger (from APPROVED status)."""
    return await ap_web_service.bulk_post_invoices_response(request, auth, db)


@router.get("/payments", response_class=HTMLResponse)
def list_payments(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    search: str | None = None,
    supplier_id: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = Query(default=1, ge=1),
    sort: str | None = None,
    sort_dir: str | None = None,
    db: Session = Depends(get_db),
):
    """AP payments list page."""
    return ap_web_service.list_payments_response(
        request,
        auth,
        search,
        supplier_id,
        status,
        start_date,
        end_date,
        page,
        db,
        sort,
        sort_dir,
    )


@router.get("/payments/new", response_class=HTMLResponse)
def new_payment_form(
    request: Request,
    invoice_id: str | None = Query(None),
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New AP payment form page."""
    return ap_web_service.payment_new_form_response(
        request, auth, db, invoice_id=invoice_id
    )


@router.get("/payments/export")
async def export_all_ap_payments(
    request: Request,
    search: str = "",
    status: str = "",
    supplier_id: str = "",
    start_date: str = "",
    end_date: str = "",
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Export all AP payments matching filters to CSV."""
    return await ap_web_service.export_all_payments_response(
        auth, db, search, status, start_date, end_date, supplier_id
    )


@router.get("/payments/{payment_id}", response_class=HTMLResponse)
def view_payment(
    request: Request,
    payment_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """AP payment detail page."""
    return ap_web_service.payment_detail_response(request, auth, db, payment_id)


@router.post("/payments/new")
async def create_payment(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Handle AP payment form submission."""
    return await ap_web_service.create_payment_response(request, auth, db)


@router.get("/payments/{payment_id}/edit", response_class=HTMLResponse)
def edit_payment_form(
    request: Request,
    payment_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Edit AP payment form page."""
    return ap_web_service.payment_edit_form_response(request, auth, db, payment_id)


@router.post("/payments/{payment_id}/edit")
async def update_payment(
    request: Request,
    payment_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Handle AP payment update form submission."""
    return await ap_web_service.update_payment_response(request, auth, db, payment_id)


@router.post("/payments/{payment_id}/approve")
def approve_payment(
    request: Request,
    payment_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Approve AP payment."""
    return ap_web_service.approve_payment_response(request, auth, db, payment_id)


@router.post("/payments/{payment_id}/post")
def post_payment(
    request: Request,
    payment_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Post AP payment to general ledger."""
    return ap_web_service.post_payment_response(request, auth, db, payment_id)


@router.post("/payments/{payment_id}/void")
def void_payment(
    request: Request,
    payment_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Void an AP payment."""
    return ap_web_service.void_payment_response(request, auth, db, payment_id)


@router.post("/payments/{payment_id}/delete")
def delete_payment(
    request: Request,
    payment_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Delete an AP payment."""
    return ap_web_service.delete_payment_response(request, auth, db, payment_id)


# ═══════════════════════════════════════════════════════════════════
# Bulk Actions - Payments
# ═══════════════════════════════════════════════════════════════════


@router.post("/payments/bulk-delete")
async def bulk_delete_payments(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Bulk delete AP payments (only DRAFT status)."""
    return await ap_web_service.bulk_delete_payments_response(request, auth, db)


@router.post("/payments/bulk-export")
async def bulk_export_payments(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Export selected AP payments to CSV."""
    return await ap_web_service.bulk_export_payments_response(request, auth, db)


@router.get("/payment-batches", response_class=HTMLResponse)
def list_payment_batches(
    request: Request,
    auth: WebAuthContext = Depends(require_web_permission("ap:payment_batches:read")),
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Payment batches list page."""
    return ap_web_service.list_payment_batches_response(request, auth, status, page, db)


@router.get("/payment-batches/new", response_class=HTMLResponse)
def new_payment_batch_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_permission("ap:payment_batches:create")),
    db: Session = Depends(get_db),
):
    """New payment batch form page."""
    return ap_web_service.payment_batch_new_form_response(request, auth, db)


@router.post("/payment-batches/new")
async def create_payment_batch(
    request: Request,
    auth: WebAuthContext = Depends(require_web_permission("ap:payment_batches:create")),
    db: Session = Depends(get_db),
):
    """Handle payment batch form submission."""
    return await ap_web_service.create_payment_batch_response(request, auth, db)


@router.get("/purchase-orders", response_class=HTMLResponse)
def list_purchase_orders(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    search: str | None = None,
    supplier_id: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Purchase orders list page."""
    return ap_web_service.list_purchase_orders_response(
        request,
        auth,
        search,
        supplier_id,
        status,
        start_date,
        end_date,
        page,
        db,
    )


@router.get("/purchase-orders/new", response_class=HTMLResponse)
def new_purchase_order_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """New purchase order form page."""
    return ap_web_service.purchase_order_new_form_response(request, auth, db)


@router.get("/purchase-orders/{po_id}", response_class=HTMLResponse)
def view_purchase_order(
    request: Request,
    po_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Purchase order detail page."""
    return ap_web_service.purchase_order_detail_response(request, auth, db, po_id)


@router.get("/purchase-orders/{po_id}/edit", response_class=HTMLResponse)
def edit_purchase_order_form(
    request: Request,
    po_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Edit purchase order form page."""
    return ap_web_service.purchase_order_edit_form_response(request, auth, db, po_id)


@router.post("/purchase-orders/new")
async def create_purchase_order(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Handle purchase order form submission."""
    return await ap_web_service.create_purchase_order_response(request, auth, db)


@router.post("/purchase-orders/{po_id}/edit")
async def update_purchase_order(
    request: Request,
    po_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Handle purchase order edit form submission."""
    return await ap_web_service.update_purchase_order_response(request, auth, db, po_id)


@router.post("/purchase-orders/{po_id}/delete")
def delete_purchase_order(
    request: Request,
    po_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Handle purchase order deletion."""
    return ap_web_service.delete_purchase_order_response(request, auth, db, po_id)


@router.post("/purchase-orders/{po_id}/submit")
def submit_purchase_order(
    request: Request,
    po_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Submit purchase order for approval."""
    return ap_web_service.submit_purchase_order_response(request, auth, db, po_id)


@router.post("/purchase-orders/{po_id}/approve")
def approve_purchase_order(
    request: Request,
    po_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Approve purchase order."""
    return ap_web_service.approve_purchase_order_response(request, auth, db, po_id)


@router.post("/purchase-orders/{po_id}/cancel")
def cancel_purchase_order(
    request: Request,
    po_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Cancel purchase order."""
    return ap_web_service.cancel_purchase_order_response(request, auth, db, po_id)


@router.get("/goods-receipts", response_class=HTMLResponse)
def list_goods_receipts(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    search: str | None = None,
    supplier_id: str | None = None,
    po_id: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Goods receipts list page."""
    return ap_web_service.list_goods_receipts_response(
        request,
        auth,
        search,
        supplier_id,
        po_id,
        status,
        start_date,
        end_date,
        page,
        db,
    )


@router.get("/goods-receipts/new", response_class=HTMLResponse)
def new_goods_receipt_form(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    po_id: str | None = None,
    db: Session = Depends(get_db),
):
    """New goods receipt form page."""
    return ap_web_service.goods_receipt_new_form_response(request, auth, po_id, db)


@router.get("/goods-receipts/{receipt_id}", response_class=HTMLResponse)
def view_goods_receipt(
    request: Request,
    receipt_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Goods receipt detail page."""
    return ap_web_service.goods_receipt_detail_response(request, auth, db, receipt_id)


@router.post("/goods-receipts/new")
async def create_goods_receipt(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Handle goods receipt form submission."""
    return await ap_web_service.create_goods_receipt_response(request, auth, db)


@router.post("/goods-receipts/{receipt_id}/inspect")
def start_inspection(
    request: Request,
    receipt_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Start inspection on goods receipt."""
    return ap_web_service.start_inspection_response(request, auth, db, receipt_id)


@router.post("/goods-receipts/{receipt_id}/accept")
def accept_all(
    request: Request,
    receipt_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Accept all items on goods receipt."""
    return ap_web_service.accept_all_response(request, auth, db, receipt_id)


@router.get("/aging", response_class=HTMLResponse)
def aging_report(
    request: Request,
    auth: WebAuthContext = Depends(require_finance_access),
    as_of_date: str | None = None,
    supplier_id: str | None = None,
    db: Session = Depends(get_db),
):
    """AP aging report page."""
    return ap_web_service.aging_report_response(
        request, auth, as_of_date, supplier_id, db
    )


@router.post("/invoices/{invoice_id}/attachments")
async def upload_invoice_attachment(
    request: Request,
    invoice_id: str,
    file: UploadFile = File(...),
    description: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Upload an attachment for a supplier invoice."""
    return await ap_web_service.upload_invoice_attachment_response(
        request,
        invoice_id,
        file,
        description,
        auth,
        db,
    )


@router.post("/purchase-orders/{po_id}/attachments")
async def upload_po_attachment(
    po_id: str,
    file: UploadFile = File(...),
    description: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Upload an attachment for a purchase order."""
    return await ap_web_service.upload_po_attachment_response(
        po_id,
        file,
        description,
        auth,
        db,
    )


@router.post("/goods-receipts/{receipt_id}/attachments")
async def upload_goods_receipt_attachment(
    receipt_id: str,
    file: UploadFile = File(...),
    description: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Upload an attachment for a goods receipt."""
    return await ap_web_service.upload_goods_receipt_attachment_response(
        receipt_id,
        file,
        description,
        auth,
        db,
    )


@router.post("/payments/{payment_id}/attachments")
async def upload_payment_attachment(
    payment_id: str,
    file: UploadFile = File(...),
    description: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Upload an attachment for a supplier payment."""
    return await ap_web_service.upload_payment_attachment_response(
        payment_id,
        file,
        description,
        auth,
        db,
    )


@router.post("/suppliers/{supplier_id}/attachments")
async def upload_supplier_attachment(
    supplier_id: str,
    file: UploadFile = File(...),
    description: str | None = None,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Upload an attachment for a supplier."""
    return await ap_web_service.upload_supplier_attachment_response(
        supplier_id,
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
    return ap_web_service.download_attachment_response(attachment_id, auth, db)


@router.post("/attachments/{attachment_id}/delete")
def delete_attachment(
    attachment_id: str,
    auth: WebAuthContext = Depends(require_finance_access),
    db: Session = Depends(get_db),
):
    """Delete an attachment."""
    return ap_web_service.delete_attachment_response(attachment_id, auth, db)
