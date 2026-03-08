"""
AP Invoice Bulk Action Service.

Provides bulk operations for AP (supplier) invoice documents.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from fastapi import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
)
from app.schemas.bulk_actions import BulkActionResult
from app.services.bulk_actions import BulkActionService
from app.services.finance.ap.supplier_invoice import SupplierInvoiceService

logger = logging.getLogger(__name__)


class APInvoiceBulkService(BulkActionService[SupplierInvoice]):
    """
    Bulk operations for AP (supplier) invoices.

    Supported actions:
    - delete: Remove invoices (only DRAFT status with no payments)
    - void: Mark invoices as VOID
    - export: Export to CSV
    """

    model = SupplierInvoice
    id_field = "invoice_id"
    org_field = "organization_id"
    search_fields = ["invoice_number", "supplier_invoice_number"]
    date_field = "invoice_date"

    # Fields to export in CSV
    export_fields = [
        ("invoice_number", "Invoice Number"),
        ("supplier_invoice_number", "Supplier Invoice #"),
        ("invoice_date", "Invoice Date"),
        ("received_date", "Received Date"),
        ("due_date", "Due Date"),
        ("supplier_name", "Supplier"),
        ("currency_code", "Currency"),
        ("subtotal", "Subtotal"),
        ("tax_amount", "Tax Amount"),
        ("withholding_tax_amount", "WHT Amount"),
        ("total_amount", "Total Amount"),
        ("amount_paid", "Amount Paid"),
        ("balance_due", "Balance Due"),
        ("status", "Status"),
        ("three_way_match_status", "Match Status"),
    ]

    async def export_all(
        self,
        search: str = "",
        status: str = "",
        start_date: str = "",
        end_date: str = "",
        extra_filters: dict[str, object] | None = None,
        format: str = "csv",
    ) -> Response:
        """
        Export all supplier invoices matching filters to CSV.

        Uses the shared query builder to match list-page filtering.
        """
        from app.services.finance.ap.invoice_query import build_invoice_query

        supplier_id = ""
        if extra_filters:
            supplier_id = str(extra_filters.get("supplier_id") or "")

        stmt = build_invoice_query(
            db=self.db,
            organization_id=str(self.organization_id),
            search=search,
            supplier_id=supplier_id or None,
            status=status,
            start_date=start_date,
            end_date=end_date,
        )

        entities = list(self.db.scalars(stmt).all())
        return self._build_csv(entities)

    def can_delete(self, entity: SupplierInvoice) -> tuple[bool, str]:
        """
        Check if a supplier invoice can be deleted.

        A supplier invoice can only be deleted if:
        - Status is DRAFT
        - No payments have been applied
        - Not posted to GL
        """
        # Only DRAFT invoices can be deleted
        if entity.status != SupplierInvoiceStatus.DRAFT:
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

    _supplier_names: dict[str, str] | None = None

    def _resolve_supplier_name(self, supplier_id: object) -> str:
        """Batch-resolve supplier names on first call, then use cache."""
        if self._supplier_names is None:
            from app.models.finance.ap.supplier import Supplier

            rows = self.db.execute(
                select(
                    Supplier.supplier_id,
                    Supplier.trading_name,
                    Supplier.legal_name,
                ).where(Supplier.organization_id == self.organization_id)
            ).all()
            self._supplier_names = {str(r[0]): r[1] or r[2] or "" for r in rows}
        return self._supplier_names.get(str(supplier_id), "")

    def _get_export_value(self, entity: SupplierInvoice, field_name: str) -> str:
        """Handle special field formatting for supplier invoice export."""
        if field_name == "status":
            return entity.status.value if entity.status else ""
        if field_name == "invoice_type":
            return entity.invoice_type.value if entity.invoice_type else ""
        if field_name in ("invoice_date", "due_date", "received_date"):
            val = getattr(entity, field_name, None)
            return val.isoformat() if val else ""
        if field_name == "balance_due":
            return str(entity.balance_due)
        if field_name == "supplier_name":
            return self._resolve_supplier_name(entity.supplier_id)

        return str(super()._get_export_value(entity, field_name))

    def _get_export_filename(self) -> str:
        """Get supplier invoice export filename."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"ap_invoices_export_{timestamp}.csv"

    async def bulk_approve(self, ids: list[UUID]) -> BulkActionResult:
        """
        Approve multiple supplier invoices.

        Only invoices in SUBMITTED status can be approved.
        Respects segregation of duties - approver cannot be submitter.
        """
        if not ids:
            return BulkActionResult.failure("No IDs provided")

        entities = self._get_entities(ids)
        if not entities:
            return BulkActionResult.failure("No invoices found with provided IDs")

        if not self.user_id:
            return BulkActionResult.failure("User required for approval")
        user_id = self.user_id

        success_count = 0
        failed_count = 0
        errors: list[str] = []

        for invoice in entities:
            try:
                SupplierInvoiceService.approve_invoice(
                    self.db,
                    self.organization_id,
                    invoice.invoice_id,
                    user_id,
                )
                success_count += 1
            except Exception as e:
                failed_count += 1
                errors.append(f"{invoice.invoice_number}: {str(e)}")

        if success_count > 0:
            self.db.commit()

        if failed_count > 0:
            return BulkActionResult.partial(success_count, failed_count, errors)

        return BulkActionResult.success(
            success_count, f"Approved {success_count} invoices"
        )

    async def bulk_post(self, ids: list[UUID]) -> BulkActionResult:
        """
        Post multiple supplier invoices to the General Ledger.

        Only invoices in APPROVED status can be posted.
        Creates journal entries for each invoice.
        """
        if not ids:
            return BulkActionResult.failure("No IDs provided")

        entities = self._get_entities(ids)
        if not entities:
            return BulkActionResult.failure("No invoices found with provided IDs")

        if not self.user_id:
            return BulkActionResult.failure("User required for posting")
        user_id = self.user_id

        success_count = 0
        failed_count = 0
        errors: list[str] = []

        for invoice in entities:
            try:
                SupplierInvoiceService.post_invoice(
                    self.db,
                    self.organization_id,
                    invoice.invoice_id,
                    user_id,
                )
                success_count += 1
            except Exception as e:
                failed_count += 1
                errors.append(f"{invoice.invoice_number}: {str(e)}")

        if success_count > 0:
            self.db.commit()

        if failed_count > 0:
            return BulkActionResult.partial(success_count, failed_count, errors)

        return BulkActionResult.success(
            success_count, f"Posted {success_count} invoices to ledger"
        )


def get_ap_invoice_bulk_service(
    db: Session,
    organization_id: UUID,
    user_id: UUID | None = None,
) -> APInvoiceBulkService:
    """Factory function to create an APInvoiceBulkService instance."""
    return APInvoiceBulkService(db, organization_id, user_id)
