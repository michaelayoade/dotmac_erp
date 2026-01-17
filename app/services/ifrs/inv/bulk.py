"""
INV Item Bulk Action Service.

Provides bulk operations for inventory items.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.ifrs.inv.item import Item
from app.models.ifrs.inv.transaction import InventoryTransaction
from app.services.bulk_actions import BulkActionService


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
        transaction_count = (
            self.db.query(InventoryTransaction)
            .filter(InventoryTransaction.item_id == entity.item_id)
            .count()
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

        return super()._get_export_value(entity, field_name)

    def _get_export_filename(self) -> str:
        """Get item export filename."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"items_export_{timestamp}.csv"


def get_item_bulk_service(
    db: Session,
    organization_id: UUID,
    user_id: UUID | None = None,
) -> ItemBulkService:
    """Factory function to create an ItemBulkService instance."""
    return ItemBulkService(db, organization_id, user_id)
