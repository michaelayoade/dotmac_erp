"""
AR Web Service - Modular web view services for AR module.

This module provides a facade that maintains backward compatibility
while organizing code into resource-specific submodules:
- base.py - Common utilities and view transformers
- customer_web.py - Customer-related methods
- invoice_web.py - Invoice-related methods
- receipt_web.py - Receipt/payment-related methods
- credit_note_web.py - Credit note-related methods

Usage:
    from app.services.finance.ar.web import ar_web_service
    # Or import the class:
    from app.services.finance.ar.web import ARWebService

For backward compatibility, the original import path also works:
    from app.services.finance.ar.web import ar_web_service
"""

from fastapi import Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.schemas.bulk_actions import BulkActionRequest, BulkExportRequest
from app.services.finance.common.attachment import attachment_service
from app.web.deps import WebAuthContext

from app.services.finance.ar.web.base import (
    # Parsing utilities
    parse_date,
    format_date,
    format_currency,
    format_file_size,
    parse_customer_type,
    parse_invoice_status,
    parse_receipt_status,
    # Display utilities
    customer_display_name,
    invoice_status_label,
    receipt_status_label,
    # View transformers
    customer_option_view,
    customer_form_view,
    customer_list_view,
    customer_detail_view,
    invoice_line_view,
    invoice_detail_view,
    receipt_detail_view,
    allocation_view,
    # Reference data queries
    get_accounts,
    get_cost_centers,
    get_projects,
    calculate_customer_balance_trends,
    # Data classes
    InvoiceStats,
)

# Import the modular service components
from app.services.finance.ar.web.customer_web import CustomerWebService
from app.services.finance.ar.web.invoice_web import InvoiceWebService
from app.services.finance.ar.web.receipt_web import ReceiptWebService
from app.services.finance.ar.web.credit_note_web import CreditNoteWebService
from app.services.finance.ar.web.quote_web import QuoteWebService, quote_web_service
from app.services.finance.ar.web.sales_order_web import SalesOrderWebService, sales_order_web_service


class ARWebService(
    CustomerWebService,
    InvoiceWebService,
    ReceiptWebService,
    CreditNoteWebService,
    QuoteWebService,
    SalesOrderWebService,
):
    """
    Unified AR Web Service facade.

    Combines customer, invoice, receipt, and credit note web services into a single
    interface for backward compatibility.

    This class inherits from:
    - CustomerWebService: Customer listing, creation, editing
    - InvoiceWebService: Invoice management
    - ReceiptWebService: Receipt/payment and aging reports
    - CreditNoteWebService: Credit note management
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
            return RedirectResponse(url="/finance/ar/invoices?error=Attachment+not+found", status_code=303)

        file_path = attachment_service.get_file_path(attachment)

        if not file_path.exists():
            return RedirectResponse(url="/finance/ar/invoices?error=File+not+found", status_code=303)

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
            return RedirectResponse(url="/finance/ar/invoices?error=Attachment+not+found", status_code=303)

        entity_type = attachment.entity_type
        entity_id = attachment.entity_id

        attachment_service.delete(db, attachment_id, auth.organization_id)

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

    # =====================================================================
    # Bulk Action Methods - Customers
    # =====================================================================

    async def bulk_delete_customers_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        """Handle bulk delete customers request."""
        from app.services.finance.ar.bulk import get_customer_bulk_service

        body = await request.json()
        req = BulkActionRequest(**body)
        service = get_customer_bulk_service(db, auth.organization_id, auth.user_id)
        return await service.bulk_delete(req.ids)

    async def bulk_export_customers_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        """Handle bulk export customers request."""
        from app.services.finance.ar.bulk import get_customer_bulk_service

        body = await request.json()
        req = BulkExportRequest(**body)
        service = get_customer_bulk_service(db, auth.organization_id, auth.user_id)
        return await service.bulk_export(req.ids, req.format)

    async def bulk_activate_customers_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        """Handle bulk activate customers request."""
        from app.services.finance.ar.bulk import get_customer_bulk_service

        body = await request.json()
        req = BulkActionRequest(**body)
        service = get_customer_bulk_service(db, auth.organization_id, auth.user_id)
        return await service.bulk_activate(req.ids)

    async def bulk_deactivate_customers_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        """Handle bulk deactivate customers request."""
        from app.services.finance.ar.bulk import get_customer_bulk_service

        body = await request.json()
        req = BulkActionRequest(**body)
        service = get_customer_bulk_service(db, auth.organization_id, auth.user_id)
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
        from app.services.finance.ar.invoice_bulk import get_ar_invoice_bulk_service

        body = await request.json()
        req = BulkActionRequest(**body)
        service = get_ar_invoice_bulk_service(db, auth.organization_id, auth.user_id)
        return await service.bulk_delete(req.ids)

    async def bulk_export_invoices_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        """Handle bulk export invoices request."""
        from app.services.finance.ar.invoice_bulk import get_ar_invoice_bulk_service

        body = await request.json()
        req = BulkExportRequest(**body)
        service = get_ar_invoice_bulk_service(db, auth.organization_id, auth.user_id)
        return await service.bulk_export(req.ids, req.format)

    # =====================================================================
    # Bulk Action Methods - Receipts
    # =====================================================================

    async def bulk_delete_receipts_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        """Handle bulk delete receipts request."""
        from app.services.finance.ar.receipt_bulk import get_ar_receipt_bulk_service

        body = await request.json()
        req = BulkActionRequest(**body)
        service = get_ar_receipt_bulk_service(db, auth.organization_id, auth.user_id)
        return await service.bulk_delete(req.ids)

    async def bulk_export_receipts_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ):
        """Handle bulk export receipts request."""
        from app.services.finance.ar.receipt_bulk import get_ar_receipt_bulk_service

        body = await request.json()
        req = BulkExportRequest(**body)
        service = get_ar_receipt_bulk_service(db, auth.organization_id, auth.user_id)
        return await service.bulk_export(req.ids, req.format)


# Module-level singleton for backward compatibility
ar_web_service = ARWebService()


__all__ = [
    # Utilities
    "parse_date",
    "format_date",
    "format_currency",
    "format_file_size",
    "parse_customer_type",
    "parse_invoice_status",
    "parse_receipt_status",
    # Display utilities
    "customer_display_name",
    "invoice_status_label",
    "receipt_status_label",
    # View transformers
    "customer_option_view",
    "customer_form_view",
    "customer_list_view",
    "customer_detail_view",
    "invoice_line_view",
    "invoice_detail_view",
    "receipt_detail_view",
    "allocation_view",
    # Reference data queries
    "get_accounts",
    "get_cost_centers",
    "get_projects",
    "calculate_customer_balance_trends",
    # Data classes
    "InvoiceStats",
    # Service classes
    "CustomerWebService",
    "InvoiceWebService",
    "ReceiptWebService",
    "CreditNoteWebService",
    "QuoteWebService",
    "SalesOrderWebService",
    "ARWebService",
    # Singletons
    "ar_web_service",
    "quote_web_service",
    "sales_order_web_service",
]
