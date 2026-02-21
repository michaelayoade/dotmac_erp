"""
Shared fixed asset query builder for list + export.
"""

from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Query, Session

from app.models.fixed_assets.asset import Asset, AssetStatus
from app.models.fixed_assets.asset_category import AssetCategory
from app.services.common import coerce_uuid


def _try_uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return cast(UUID, coerce_uuid(value, raise_http=False))
    except (TypeError, ValueError):
        return None


def _parse_status(value: str | None) -> AssetStatus | None:
    if not value:
        return None
    try:
        return AssetStatus(value)
    except ValueError:
        try:
            return AssetStatus(value.upper())
        except ValueError:
            return None
    return None


def build_asset_query(
    db: Session,
    organization_id: str,
    search: str | None = None,
    category: str | None = None,
    status: str | None = None,
) -> Query:
    """
    Build the base asset query with filters applied.
    """
    org_id = coerce_uuid(organization_id)
    status_value = _parse_status(status)
    category_id = _try_uuid(category)

    query = db.query(Asset).join(
        AssetCategory, Asset.category_id == AssetCategory.category_id
    )
    query = query.filter(Asset.organization_id == org_id)

    if status_value:
        query = query.filter(Asset.status == status_value)
    if category_id:
        query = query.filter(Asset.category_id == category_id)
    elif category:
        query = query.filter(AssetCategory.category_code == category)
    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            or_(
                Asset.asset_number.ilike(search_pattern),
                Asset.asset_name.ilike(search_pattern),
                Asset.serial_number.ilike(search_pattern),
                Asset.barcode.ilike(search_pattern),
            )
        )

    return query
