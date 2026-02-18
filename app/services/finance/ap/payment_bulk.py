"""
AP Payment (Supplier Payment) Bulk Action Service.

Provides bulk operations for AP payment documents.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from fastapi import Response
from sqlalchemy import select
from sqlalchemy.orm import Query, Session

from app.models.finance.ap.supplier_payment import APPaymentStatus, SupplierPayment
from app.services.bulk_actions import BulkActionService

logger = logging.getLogger(__name__)


class APPaymentBulkService(BulkActionService[SupplierPayment]):
    """
    Bulk operations for AP payments (supplier payments).

    Supported actions:
    - delete: Remove payments (only DRAFT status with no allocations)
    - export: Export to CSV
    """

    model = SupplierPayment
    id_field = "payment_id"
    org_field = "organization_id"
    search_fields = ["payment_number", "reference"]
    date_field = "payment_date"

    # Fields to export in CSV
    export_fields = [
        ("payment_number", "Payment Number"),
        ("payment_date", "Payment Date"),
        ("supplier_name", "Supplier"),
        ("payment_method", "Payment Method"),
        ("currency_code", "Currency"),
        ("gross_amount", "Gross Amount"),
        ("withholding_tax_amount", "WHT Amount"),
        ("amount", "Net Amount"),
        ("reference", "Reference"),
        ("status", "Status"),
        ("remittance_advice_sent", "Remittance Sent"),
    ]

    def can_delete(self, entity: SupplierPayment) -> tuple[bool, str]:
        """
        Check if a payment can be deleted.

        A payment can only be deleted if:
        - Status is DRAFT (not yet submitted for approval)
        - Not posted to GL
        - Not reconciled with bank
        - Has no allocations to invoices
        """
        # Only DRAFT payments can be deleted
        if entity.status != APPaymentStatus.DRAFT:
            return (
                False,
                f"Cannot delete payment '{entity.payment_number}': only DRAFT payments can be deleted (current status: {entity.status.value})",
            )

        # Check if posted to GL
        if entity.journal_entry_id is not None:
            return (
                False,
                f"Cannot delete payment '{entity.payment_number}': already posted to General Ledger",
            )

        # Check if reconciled with bank
        if entity.bank_reconciliation_id is not None:
            return (
                False,
                f"Cannot delete payment '{entity.payment_number}': already reconciled with bank statement",
            )

        # Check for payment allocations
        from app.models.finance.ap.ap_payment_allocation import APPaymentAllocation

        allocation_count = self.db.scalar(
            select(APPaymentAllocation)
            .where(APPaymentAllocation.payment_id == entity.payment_id)
            .limit(1)
        )
        if allocation_count is not None:
            return (
                False,
                f"Cannot delete payment '{entity.payment_number}': has allocations to invoices",
            )

        return (True, "")

    _supplier_names: dict[str, str] | None = None

    def _resolve_supplier_name(self, supplier_id: object) -> str:
        """Batch-resolve supplier names on first call, then use cache."""
        if self._supplier_names is None:
            from app.models.finance.ap.supplier import Supplier

            rows = (
                Query(
                    [
                        Supplier.supplier_id,
                        Supplier.trading_name,
                        Supplier.legal_name,
                    ],
                    session=self.db,
                )
                .filter(Supplier.organization_id == self.organization_id)
                .all()
            )
            self._supplier_names = {str(r[0]): r[1] or r[2] or "" for r in rows}
        return self._supplier_names.get(str(supplier_id), "")

    def _get_export_value(self, entity: SupplierPayment, field_name: str) -> str:
        """Handle special field formatting for payment export."""
        if field_name == "status":
            return entity.status.value if entity.status else ""
        if field_name == "payment_method":
            return entity.payment_method.value if entity.payment_method else ""
        if field_name == "payment_date":
            return entity.payment_date.isoformat() if entity.payment_date else ""
        if field_name == "supplier_name":
            return self._resolve_supplier_name(entity.supplier_id)
        if field_name == "remittance_advice_sent":
            return "Yes" if entity.remittance_advice_sent else "No"

        return str(super()._get_export_value(entity, field_name))

    def _get_export_filename(self) -> str:
        """Get payment export filename."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"ap_payments_export_{timestamp}.csv"

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
        Export all payments matching filters to CSV.
        """
        from app.services.finance.ap.payment_query import build_payment_query

        supplier_id = ""
        if extra_filters:
            supplier_id = str(extra_filters.get("supplier_id") or "")

        query = build_payment_query(
            db=self.db,
            organization_id=str(self.organization_id),
            search=search,
            supplier_id=supplier_id or None,
            status=status,
            start_date=start_date,
            end_date=end_date,
        )

        entities = query.all()
        return self._build_csv(entities)


def get_ap_payment_bulk_service(
    db: Session,
    organization_id: UUID,
    user_id: UUID | None = None,
) -> APPaymentBulkService:
    """Factory function to create an APPaymentBulkService instance."""
    return APPaymentBulkService(db, organization_id, user_id)
