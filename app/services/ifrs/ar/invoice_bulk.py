"""
AR Invoice Bulk Action Service.

Provides bulk operations for AR invoice documents.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.ifrs.ar.invoice import Invoice, InvoiceStatus
from app.services.bulk_actions import BulkActionService


class ARInvoiceBulkService(BulkActionService[Invoice]):
    """
    Bulk operations for AR invoices.

    Supported actions:
    - delete: Remove invoices (only DRAFT status with no payments)
    - void: Mark invoices as VOID
    - export: Export to CSV
    """

    model = Invoice
    id_field = "invoice_id"
    org_field = "organization_id"

    # Fields to export in CSV
    export_fields = [
        ("invoice_number", "Invoice Number"),
        ("invoice_date", "Invoice Date"),
        ("due_date", "Due Date"),
        ("customer_name", "Customer"),
        ("currency_code", "Currency"),
        ("subtotal", "Subtotal"),
        ("tax_amount", "Tax Amount"),
        ("total_amount", "Total Amount"),
        ("amount_paid", "Amount Paid"),
        ("balance_due", "Balance Due"),
        ("status", "Status"),
    ]

    def can_delete(self, entity: Invoice) -> tuple[bool, str]:
        """
        Check if an invoice can be deleted.

        An invoice can only be deleted if:
        - Status is DRAFT
        - No payments have been applied
        - Not posted to GL
        """
        # Only DRAFT invoices can be deleted
        if entity.status != InvoiceStatus.DRAFT:
            return (
                False,
                f"Cannot delete invoice '{entity.invoice_number}': only DRAFT invoices can be deleted (current status: {entity.status.value})",
            )

        # Check if any payments have been applied
        if entity.amount_paid > 0:
            return (
                False,
                f"Cannot delete invoice '{entity.invoice_number}': has payments applied (₦{entity.amount_paid:,.2f})",
            )

        # Check if posted to GL
        if entity.journal_entry_id is not None:
            return (
                False,
                f"Cannot delete invoice '{entity.invoice_number}': already posted to General Ledger",
            )

        return (True, "")

    def _get_export_value(self, entity: Invoice, field_name: str) -> str:
        """Handle special field formatting for invoice export."""
        if field_name == "status":
            return entity.status.value if entity.status else ""
        if field_name == "invoice_type":
            return entity.invoice_type.value if entity.invoice_type else ""
        if field_name in ("invoice_date", "due_date"):
            val = getattr(entity, field_name, None)
            return val.isoformat() if val else ""
        if field_name == "balance_due":
            return str(entity.balance_due)
        if field_name == "customer_name":
            # This would need a join - for now return empty or customer_id
            return str(entity.customer_id) if entity.customer_id else ""

        return super()._get_export_value(entity, field_name)

    def _get_export_filename(self) -> str:
        """Get invoice export filename."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"ar_invoices_export_{timestamp}.csv"


def get_ar_invoice_bulk_service(
    db: Session,
    organization_id: UUID,
    user_id: UUID | None = None,
) -> ARInvoiceBulkService:
    """Factory function to create an ARInvoiceBulkService instance."""
    return ARInvoiceBulkService(db, organization_id, user_id)
