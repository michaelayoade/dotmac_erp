"""
AP Supplier Bulk Action Service.

Provides bulk operations for supplier master data.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from fastapi import Response
from sqlalchemy.orm import Session

from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import SupplierInvoice
from app.services.bulk_actions import BulkActionService

logger = logging.getLogger(__name__)


class SupplierBulkService(BulkActionService[Supplier]):
    """
    Bulk operations for suppliers.

    Supported actions:
    - delete: Remove suppliers (if no associated invoices)
    - activate: Set is_active=True
    - deactivate: Set is_active=False
    - export: Export to CSV
    """

    model = Supplier
    id_field = "supplier_id"
    search_fields = ["supplier_name", "supplier_code", "tax_id"]
    org_field = "organization_id"

    # Fields to export in CSV
    export_fields = [
        ("supplier_code", "Supplier Code"),
        ("legal_name", "Legal Name"),
        ("trading_name", "Trading Name"),
        ("supplier_type", "Type"),
        ("tax_identification_number", "Tax ID"),
        ("registration_number", "Registration Number"),
        ("currency_code", "Currency"),
        ("payment_terms_days", "Payment Terms (Days)"),
        ("is_related_party", "Related Party"),
        ("is_active", "Active"),
        ("primary_contact.name", "Contact Name"),
        ("primary_contact.email", "Contact Email"),
        ("primary_contact.phone", "Contact Phone"),
    ]

    def can_delete(self, entity: Supplier) -> tuple[bool, str]:
        """
        Check if a supplier can be deleted.

        A supplier cannot be deleted if they have any invoices.
        """
        # Check for associated invoices
        invoice_count = (
            self.db.query(SupplierInvoice)
            .filter(SupplierInvoice.supplier_id == entity.supplier_id)
            .count()
        )

        if invoice_count > 0:
            return (
                False,
                f"Cannot delete '{entity.legal_name}': has {invoice_count} invoice(s)",
            )

        return (True, "")

    def _get_export_value(self, entity: Supplier, field_name: str) -> str:
        """Handle special field formatting for supplier export."""
        if field_name == "supplier_type":
            return entity.supplier_type.value if entity.supplier_type else ""
        if field_name.startswith("primary_contact."):
            contact = entity.primary_contact or {}
            subfield = field_name.split(".")[1]
            return str(contact.get(subfield, ""))

        return str(super()._get_export_value(entity, field_name))

    def _get_export_filename(self) -> str:
        """Get supplier export filename."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"suppliers_export_{timestamp}.csv"

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
        Export all suppliers matching filters to CSV.
        """
        from app.services.finance.ap.supplier_query import build_supplier_query

        query = build_supplier_query(
            db=self.db,
            organization_id=str(self.organization_id),
            search=search,
            status=status,
        )

        entities = query.all()
        return self._build_csv(entities)


def get_supplier_bulk_service(
    db: Session,
    organization_id: UUID,
    user_id: UUID | None = None,
) -> SupplierBulkService:
    """Factory function to create a SupplierBulkService instance."""
    return SupplierBulkService(db, organization_id, user_id)
