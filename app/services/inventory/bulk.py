"""
INV Item Bulk Action Service.

Provides bulk operations for inventory items.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from fastapi import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory.inventory_transaction import InventoryTransaction
from app.models.inventory.item import Item
from app.services.bulk_actions import BulkActionService

logger = logging.getLogger(__name__)


class ItemBulkService(BulkActionService[Item]):
    """
    Bulk operations for inventory items.

    Supported actions:
    - delete: Remove items (if no transactions)
    - activate: Set is_active=True
    - deactivate: Set is_active=False
    - export: Export to CSV
    """

    model = Item
    id_field = "item_id"
    search_fields = ["item_code", "item_name", "description"]
    org_field = "organization_id"

    # Fields to export in CSV
    export_fields = [
        ("item_code", "Item Code"),
        ("item_name", "Item Name"),
        ("description", "Description"),
        ("item_type", "Item Type"),
        ("category_id", "Category"),
        ("unit_of_measure", "UOM"),
        ("standard_cost", "Standard Cost"),
        ("sales_price", "Sales Price"),
        ("purchase_price", "Purchase Price"),
        ("is_active", "Active"),
        ("is_stockable", "Stockable"),
        ("is_sellable", "Sellable"),
        ("is_purchasable", "Purchasable"),
    ]

    def can_delete(self, entity: Item) -> tuple[bool, str]:
        """
        Check if an item can be deleted.

        An item cannot be deleted if it has inventory transactions.
        """
        # Check for transactions
        from sqlalchemy import func

        transaction_count = (
            self.db.scalar(
                select(func.count())
                .select_from(InventoryTransaction)
                .where(InventoryTransaction.item_id == entity.item_id)
            )
            or 0
        )

        if transaction_count > 0:
            return (
                False,
                f"Cannot delete '{entity.item_name}': has {transaction_count} transaction(s)",
            )

        return (True, "")

    def _get_export_value(self, entity: Item, field_name: str) -> str:
        """Handle special field formatting for item export."""
        if field_name == "item_type":
            return entity.item_type.value if entity.item_type else ""

        return str(super()._get_export_value(entity, field_name))

    def _get_export_filename(self) -> str:
        """Get item export filename."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"items_export_{timestamp}.csv"

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
        Export all items matching filters to CSV.
        """
        from app.services.inventory.item_query import build_item_query

        category = ""
        if extra_filters:
            category = str(
                extra_filters.get("category") or extra_filters.get("category_id") or ""
            )

        query = build_item_query(
            db=self.db,
            organization_id=str(self.organization_id),
            search=search,
            category=category or None,
            status=status,
        )

        entities = list(self.db.scalars(query).all())
        return self._build_csv(entities)


def get_item_bulk_service(
    db: Session,
    organization_id: UUID,
    user_id: UUID | None = None,
) -> ItemBulkService:
    """Factory function to create an ItemBulkService instance."""
    return ItemBulkService(db, organization_id, user_id)
