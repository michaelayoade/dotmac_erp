"""
AR Invoice Bulk Action Service.

Provides bulk operations for AR invoice documents.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.finance.ar.invoice import Invoice, InvoiceStatus
from app.schemas.bulk_actions import BulkActionResult
from app.services.bulk_actions import BulkActionService
from app.services.finance.ar.invoice import ARInvoiceService


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

        return str(super()._get_export_value(entity, field_name))

    def _get_export_filename(self) -> str:
        """Get invoice export filename."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"ar_invoices_export_{timestamp}.csv"

    async def bulk_approve(self, ids: list[UUID]) -> BulkActionResult:
        """
        Approve multiple invoices.

        Only invoices in SUBMITTED status can be approved.
        Respects segregation of duties - approver cannot be submitter.
        """
        if not ids:
            return BulkActionResult.failure("No IDs provided")

        entities = self._get_entities(ids)
        if not entities:
            return BulkActionResult.failure("No invoices found with provided IDs")

        success_count = 0
        failed_count = 0
        errors: list[str] = []

        for invoice in entities:
            try:
                ARInvoiceService.approve_invoice(
                    self.db,
                    self.organization_id,
                    invoice.invoice_id,
                    self.user_id,
                )
                success_count += 1
            except Exception as e:
                failed_count += 1
                errors.append(f"{invoice.invoice_number}: {str(e)}")

        if success_count > 0:
            self.db.commit()

        if failed_count > 0:
            return BulkActionResult.partial(success_count, failed_count, errors)

        return BulkActionResult.success(success_count, f"Approved {success_count} invoices")

    async def bulk_post(self, ids: list[UUID]) -> BulkActionResult:
        """
        Post multiple invoices to the General Ledger.

        Only invoices in APPROVED status can be posted.
        Creates journal entries for each invoice.
        """
        if not ids:
            return BulkActionResult.failure("No IDs provided")

        entities = self._get_entities(ids)
        if not entities:
            return BulkActionResult.failure("No invoices found with provided IDs")

        success_count = 0
        failed_count = 0
        errors: list[str] = []

        for invoice in entities:
            try:
                ARInvoiceService.post_invoice(
                    self.db,
                    self.organization_id,
                    invoice.invoice_id,
                    self.user_id,
                )
                success_count += 1
            except Exception as e:
                failed_count += 1
                errors.append(f"{invoice.invoice_number}: {str(e)}")

        if success_count > 0:
            self.db.commit()

        if failed_count > 0:
            return BulkActionResult.partial(success_count, failed_count, errors)

        return BulkActionResult.success(success_count, f"Posted {success_count} invoices to ledger")


def get_ar_invoice_bulk_service(
    db: Session,
    organization_id: UUID,
    user_id: UUID | None = None,
) -> ARInvoiceBulkService:
    """Factory function to create an ARInvoiceBulkService instance."""
    return ARInvoiceBulkService(db, organization_id, user_id)
