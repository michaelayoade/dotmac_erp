"""
Shared AR customer query builder for list + export.
"""

from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models.finance.ar.customer import Customer
from app.services.common import coerce_uuid


def build_customer_query(
    db: Session,
    organization_id: str,
    search: str | None = None,
    status: str | None = None,
    parent_customer_id: str | None = None,
    top_level_only: bool = False,
) -> Select:
    """
    Build the base AR customer query with filters applied.

    Args:
        parent_customer_id: Filter children of a specific parent.
        top_level_only: If True, only return customers with no parent.
    """
    org_id = coerce_uuid(organization_id)

    is_active = None
    if status == "active":
        is_active = True
    elif status == "inactive":
        is_active = False

    query = select(Customer).where(Customer.organization_id == org_id)

    if is_active is not None:
        query = query.where(Customer.is_active == is_active)
    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            (Customer.customer_code.ilike(search_pattern))
            | (Customer.legal_name.ilike(search_pattern))
            | (Customer.trading_name.ilike(search_pattern))
            | (Customer.tax_identification_number.ilike(search_pattern))
        )
    if parent_customer_id:
        parent_id = coerce_uuid(parent_customer_id)
        query = query.where(Customer.parent_customer_id == parent_id)
    elif top_level_only:
        query = query.where(Customer.parent_customer_id.is_(None))

    return query
