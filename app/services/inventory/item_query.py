"""
Shared inventory item query builder for list + export.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Query, Session

from app.models.inventory.item import Item
from app.models.inventory.item_category import ItemCategory
from app.services.common import coerce_uuid


def _try_uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except (TypeError, ValueError):
        return None


def build_item_query(
    db: Session,
    organization_id: str,
    search: str | None = None,
    category: str | None = None,
    status: str | None = None,
) -> Query:
    """
    Build the base inventory item query with filters applied.
    """
    org_id = coerce_uuid(organization_id)

    query = db.query(Item).join(
        ItemCategory, Item.category_id == ItemCategory.category_id
    )
    query = query.filter(Item.organization_id == org_id)

    category_id = _try_uuid(category)
    if category_id:
        query = query.filter(Item.category_id == category_id)
    elif category:
        query = query.filter(ItemCategory.category_code == category)

    if status == "active":
        query = query.filter(Item.is_active.is_(True))
    elif status == "inactive":
        query = query.filter(Item.is_active.is_(False))

    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            or_(
                Item.item_code.ilike(search_pattern),
                Item.item_name.ilike(search_pattern),
                Item.barcode.ilike(search_pattern),
            )
        )

    return query
