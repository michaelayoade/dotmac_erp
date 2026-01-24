"""
AP Web Service - Modular web view services for AP module.

This module provides a facade that maintains backward compatibility
while organizing code into resource-specific submodules:
- base.py - Common utilities and view transformers
- supplier_web.py - Supplier-related methods
- invoice_web.py - Invoice-related methods
- payment_web.py - Payment-related methods
- purchase_order_web.py - Purchase order-related methods
- goods_receipt_web.py - Goods receipt-related methods

Usage:
    from app.services.finance.ap.web import ap_web_service
    # Or import the class:
    from app.services.finance.ap.web import APWebService

For backward compatibility, the original import path also works:
    from app.services.finance.ap.web import ap_web_service
"""

from fastapi import Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.schemas.bulk_actions import BulkActionRequest, BulkExportRequest
from app.services.finance.common.attachment import attachment_service
from app.web.deps import WebAuthContext

from app.services.finance.ap.web.base import (
    # Parsing utilities
    parse_date,
    format_date,
    format_currency,
    format_file_size,
    parse_supplier_type,
    parse_invoice_status,
    parse_payment_status,
    # Display utilities
    supplier_display_name,
    invoice_status_label,
    payment_status_label,
    # View transformers
    supplier_option_view,
    supplier_form_view,
    supplier_list_view,
    supplier_detail_view,
    invoice_line_view,
    invoice_detail_view,
    payment_detail_view,
    allocation_view,
    # Reference data queries
    get_accounts,
    get_cost_centers,
    get_projects,
    calculate_supplier_balance_trends,
    # Data classes
    InvoiceStats,
)

# Import the modular service components
from app.services.finance.ap.web.supplier_web import SupplierWebService
from app.services.finance.ap.web.invoice_web import InvoiceWebService
from app.services.finance.ap.web.payment_web import PaymentWebService
from app.services.finance.ap.web.purchase_order_web import PurchaseOrderWebService
from app.services.finance.ap.web.goods_receipt_web import GoodsReceiptWebService


class APWebService(
    SupplierWebService,
    InvoiceWebService,
    PaymentWebService,
    PurchaseOrderWebService,
    GoodsReceiptWebService,
):
    """
    Unified AP Web Service facade.

    Combines supplier, invoice, payment, purchase order, and goods receipt
    web services into a single interface for backward compatibility.

    This class inherits from:
    - SupplierWebService: Supplier listing, creation, editing
    - InvoiceWebService: Invoice management
    - PaymentWebService: Payment and aging reports
    - PurchaseOrderWebService: Purchase order management
    - GoodsReceiptWebService: Goods receipt management
    """

    # =====================================================================
    # Attachment Methods (shared across all entity types)
    # =====================================================================

    def download_attachment_response(
        self,
        attachment_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> FileResponse | RedirectResponse:
        """Download an attachment file."""
        attachment = attachment_service.get(db, auth.organization_id, attachment_id)

        if not attachment or attachment.organization_id != auth.organization_id:
            return RedirectResponse(url="/finance/ap/invoices?error=Attachment+not+found", status_code=303)

        file_path = attachment_service.get_file_path(attachment)

        if not file_path.exists():
            return RedirectResponse(url="/finance/ap/invoices?error=File+not+found", status_code=303)

        return FileResponse(
            path=str(file_path),
            filename=attachment.file_name,
            media_type=attachment.content_type,
        )

    def delete_attachment_response(
        self,
        attachment_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Delete an attachment."""
        attachment = attachment_service.get(db, auth.organization_id, attachment_id)

        if not attachment or attachment.organization_id != auth.organization_id:
            return RedirectResponse(url="/finance/ap/invoices?error=Attachment+not+found", status_code=303)

        entity_type = attachment.entity_type
        entity_id = attachment.entity_id

        attachment_service.delete(db, attachment_id, auth.organization_id)

        redirect_map = {
            "SUPPLIER_INVOICE": f"/finance/ap/invoices/{entity_id}",
            "SUPPLIER_PAYMENT": f"/finance/ap/payments/{entity_id}",
            "PURCHASE_ORDER": f"/finance/ap/purchase-orders/{entity_id}",
            "GOODS_RECEIPT": f"/finance/ap/goods-receipts/{entity_id}",
            "SUPPLIER": f"/finance/ap/suppliers/{entity_id}",
        }

        redirect_url = redirect_map.get(entity_type, "/finance/ap/invoices")
        return RedirectResponse(
            url=f"{redirect_url}?success=Attachment+deleted",
            status_code=303,
        )

    # =====================================================================
    # Bulk Action Methods - Suppliers
    # =====================================================================

    async def bulk_delete_suppliers_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        """Handle bulk delete suppliers request."""
        from app.services.finance.ap.bulk import get_supplier_bulk_service

        body = await request.json()
        req = BulkActionRequest(**body)
        service = get_supplier_bulk_service(db, auth.organization_id, auth.user_id)
        return await service.bulk_delete(req.ids)

    async def bulk_export_suppliers_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        """Handle bulk export suppliers request."""
        from app.services.finance.ap.bulk import get_supplier_bulk_service

        body = await request.json()
        req = BulkExportRequest(**body)
        service = get_supplier_bulk_service(db, auth.organization_id, auth.user_id)
        return await service.bulk_export(req.ids, req.format)

    async def bulk_activate_suppliers_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        """Handle bulk activate suppliers request."""
        from app.services.finance.ap.bulk import get_supplier_bulk_service

        body = await request.json()
        req = BulkActionRequest(**body)
        service = get_supplier_bulk_service(db, auth.organization_id, auth.user_id)
        return await service.bulk_activate(req.ids)

    async def bulk_deactivate_suppliers_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        """Handle bulk deactivate suppliers request."""
        from app.services.finance.ap.bulk import get_supplier_bulk_service

        body = await request.json()
        req = BulkActionRequest(**body)
        service = get_supplier_bulk_service(db, auth.organization_id, auth.user_id)
        return await service.bulk_deactivate(req.ids)

    # =====================================================================
    # Bulk Action Methods - Invoices
    # =====================================================================

    async def bulk_delete_invoices_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        """Handle bulk delete invoices request."""
        from app.services.finance.ap.invoice_bulk import get_ap_invoice_bulk_service

        body = await request.json()
        req = BulkActionRequest(**body)
        service = get_ap_invoice_bulk_service(db, auth.organization_id, auth.user_id)
        return await service.bulk_delete(req.ids)

    async def bulk_export_invoices_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        """Handle bulk export invoices request."""
        from app.services.finance.ap.invoice_bulk import get_ap_invoice_bulk_service

        body = await request.json()
        req = BulkExportRequest(**body)
        service = get_ap_invoice_bulk_service(db, auth.organization_id, auth.user_id)
        return await service.bulk_export(req.ids, req.format)

    # =====================================================================
    # Bulk Action Methods - Payments
    # =====================================================================

    async def bulk_delete_payments_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        """Handle bulk delete payments request."""
        from app.services.finance.ap.payment_bulk import get_ap_payment_bulk_service

        body = await request.json()
        req = BulkActionRequest(**body)
        service = get_ap_payment_bulk_service(db, auth.organization_id, auth.user_id)
        return await service.bulk_delete(req.ids)

    async def bulk_export_payments_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        """Handle bulk export payments request."""
        from app.services.finance.ap.payment_bulk import get_ap_payment_bulk_service

        body = await request.json()
        req = BulkExportRequest(**body)
        service = get_ap_payment_bulk_service(db, auth.organization_id, auth.user_id)
        return await service.bulk_export(req.ids, req.format)


# Module-level singleton for backward compatibility
ap_web_service = APWebService()


__all__ = [
    # Utilities
    "parse_date",
    "format_date",
    "format_currency",
    "format_file_size",
    "parse_supplier_type",
    "parse_invoice_status",
    "parse_payment_status",
    # Display utilities
    "supplier_display_name",
    "invoice_status_label",
    "payment_status_label",
    # View transformers
    "supplier_option_view",
    "supplier_form_view",
    "supplier_list_view",
    "supplier_detail_view",
    "invoice_line_view",
    "invoice_detail_view",
    "payment_detail_view",
    "allocation_view",
    # Reference data queries
    "get_accounts",
    "get_cost_centers",
    "get_projects",
    "calculate_supplier_balance_trends",
    # Data classes
    "InvoiceStats",
    # Service classes
    "SupplierWebService",
    "InvoiceWebService",
    "PaymentWebService",
    "PurchaseOrderWebService",
    "GoodsReceiptWebService",
    "APWebService",
    # Singleton
    "ap_web_service",
]
