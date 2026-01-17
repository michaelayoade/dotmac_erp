"""
AR Receipt (Customer Payment) Bulk Action Service.

Provides bulk operations for AR receipt documents.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ifrs.ar.customer_payment import CustomerPayment, PaymentStatus
from app.services.bulk_actions import BulkActionService


class ARReceiptBulkService(BulkActionService[CustomerPayment]):
    """
    Bulk operations for AR receipts (customer payments).

    Supported actions:
    - delete: Remove receipts (only PENDING status with no allocations)
    - export: Export to CSV
    """

    model = CustomerPayment
    id_field = "payment_id"
    org_field = "organization_id"

    # Fields to export in CSV
    export_fields = [
        ("payment_number", "Receipt Number"),
        ("payment_date", "Receipt Date"),
        ("customer_name", "Customer"),
        ("payment_method", "Payment Method"),
        ("currency_code", "Currency"),
        ("gross_amount", "Gross Amount"),
        ("wht_amount", "WHT Amount"),
        ("amount", "Net Amount"),
        ("reference", "Reference"),
        ("status", "Status"),
    ]

    def can_delete(self, entity: CustomerPayment) -> tuple[bool, str]:
        """
        Check if a receipt can be deleted.

        A receipt can only be deleted if:
        - Status is PENDING (not yet cleared/approved)
        - Not posted to GL
        - Not reconciled with bank
        - Has no allocations to invoices
        """
        # Only PENDING receipts can be deleted
        if entity.status != PaymentStatus.PENDING:
            return (
                False,
                f"Cannot delete receipt '{entity.payment_number}': only PENDING receipts can be deleted (current status: {entity.status.value})",
            )

        # Check if posted to GL
        if entity.journal_entry_id is not None:
            return (
                False,
                f"Cannot delete receipt '{entity.payment_number}': already posted to General Ledger",
            )

        # Check if reconciled with bank
        if entity.bank_reconciliation_id is not None:
            return (
                False,
                f"Cannot delete receipt '{entity.payment_number}': already reconciled with bank statement",
            )

        # Check for payment allocations
        from app.models.ifrs.ar.payment_allocation import PaymentAllocation

        allocation_count = self.db.scalar(
            select(PaymentAllocation)
            .where(PaymentAllocation.payment_id == entity.payment_id)
            .limit(1)
        )
        if allocation_count is not None:
            return (
                False,
                f"Cannot delete receipt '{entity.payment_number}': has allocations to invoices",
            )

        return (True, "")

    def _get_export_value(self, entity: CustomerPayment, field_name: str) -> str:
        """Handle special field formatting for receipt export."""
        if field_name == "status":
            return entity.status.value if entity.status else ""
        if field_name == "payment_method":
            return entity.payment_method.value if entity.payment_method else ""
        if field_name == "payment_date":
            return entity.payment_date.isoformat() if entity.payment_date else ""
        if field_name == "customer_name":
            # This would need a join - for now return customer_id
            return str(entity.customer_id) if entity.customer_id else ""

        return super()._get_export_value(entity, field_name)

    def _get_export_filename(self) -> str:
        """Get receipt export filename."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"ar_receipts_export_{timestamp}.csv"


def get_ar_receipt_bulk_service(
    db: Session,
    organization_id: UUID,
    user_id: UUID | None = None,
) -> ARReceiptBulkService:
    """Factory function to create an ARReceiptBulkService instance."""
    return ARReceiptBulkService(db, organization_id, user_id)
