"""
INV Posting Helpers - Shared utilities for inventory GL posting.

Provides:
- Account resolution from items and categories
- Common validation logic
"""

from typing import Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.finance.inv.item import Item
from app.models.finance.inv.item_category import ItemCategory
from app.models.finance.inv.inventory_transaction import InventoryTransaction


def get_item_accounts(
    db: Session,
    transaction: InventoryTransaction,
) -> Tuple[Optional[Item], Optional[ItemCategory]]:
    """
    Get item and category for account resolution.

    Args:
        db: Database session
        transaction: The inventory transaction

    Returns:
        Tuple of (Item, ItemCategory) or (None, None) if not found
    """
    item = db.get(Item, transaction.item_id)
    if not item:
        return None, None

    category = db.get(ItemCategory, item.category_id)
    return item, category


def get_inventory_account(item: Item, category: ItemCategory) -> Optional[UUID]:
    """Get the inventory account for an item."""
    return item.inventory_account_id or category.inventory_account_id


def get_cogs_account(
    item: Item,
    category: ItemCategory,
    override_account_id: Optional[UUID] = None,
) -> Optional[UUID]:
    """Get the COGS account for an item, with optional override."""
    if override_account_id:
        return override_account_id
    return item.cogs_account_id or category.cogs_account_id
