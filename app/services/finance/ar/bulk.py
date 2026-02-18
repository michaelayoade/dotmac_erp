"""
AR Customer Bulk Action Service.

Provides bulk operations for customer master data.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from fastapi import Response
from sqlalchemy.orm import Query, Session

from app.models.finance.ar.customer import Customer
from app.models.finance.ar.invoice import Invoice
from app.services.bulk_actions import BulkActionService

logger = logging.getLogger(__name__)


class CustomerBulkService(BulkActionService[Customer]):
    """
    Bulk operations for customers.

    Supported actions:
    - delete: Remove customers (if no associated invoices)
    - activate: Set is_active=True
    - deactivate: Set is_active=False
    - export: Export to CSV
    """

    model = Customer
    id_field = "customer_id"
    search_fields = ["customer_name", "customer_code", "tax_id"]
    org_field = "organization_id"

    # Fields to export in CSV
    export_fields = [
        ("customer_code", "Customer Code"),
        ("legal_name", "Legal Name"),
        ("trading_name", "Trading Name"),
        ("customer_type", "Type"),
        ("tax_identification_number", "Tax ID"),
        ("registration_number", "Registration Number"),
        ("currency_code", "Currency"),
        ("credit_limit", "Credit Limit"),
        ("credit_terms_days", "Credit Terms (Days)"),
        ("credit_hold", "Credit Hold"),
        ("risk_category", "Risk Category"),
        ("is_related_party", "Related Party"),
        ("is_active", "Active"),
        ("primary_contact.name", "Contact Name"),
        ("primary_contact.email", "Contact Email"),
        ("primary_contact.phone", "Contact Phone"),
    ]

    def can_delete(self, entity: Customer) -> tuple[bool, str]:
        """
        Check if a customer can be deleted.

        A customer cannot be deleted if they have any invoices.
        """
        # Check for associated invoices
        invoice_count = (
            Query([Invoice], session=self.db)
            .filter(Invoice.customer_id == entity.customer_id)
            .count()
        )

        if invoice_count > 0:
            return (
                False,
                f"Cannot delete '{entity.legal_name}': has {invoice_count} invoice(s)",
            )

        return (True, "")

    def _get_export_value(self, entity: Customer, field_name: str) -> str:
        """Handle special field formatting for customer export."""
        if field_name == "customer_type":
            return entity.customer_type.value if entity.customer_type else ""
        if field_name == "risk_category":
            return entity.risk_category.value if entity.risk_category else ""
        if field_name.startswith("primary_contact."):
            contact = entity.primary_contact or {}
            subfield = field_name.split(".")[1]
            return str(contact.get(subfield, ""))

        return str(super()._get_export_value(entity, field_name))

    def _get_export_filename(self) -> str:
        """Get customer export filename."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"customers_export_{timestamp}.csv"

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
        Export all customers matching filters to CSV.
        """
        from app.services.finance.ar.customer_query import build_customer_query

        query = build_customer_query(
            db=self.db,
            organization_id=str(self.organization_id),
            search=search,
            status=status,
        )

        entities = query.all()
        return self._build_csv(entities)


def get_customer_bulk_service(
    db: Session,
    organization_id: UUID,
    user_id: UUID | None = None,
) -> CustomerBulkService:
    """Factory function to create a CustomerBulkService instance."""
    return CustomerBulkService(db, organization_id, user_id)
