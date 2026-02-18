"""
Shared AR customer query builder for list + export.
"""

from __future__ import annotations

from sqlalchemy.orm import Query, Session

from app.models.finance.ar.customer import Customer
from app.services.common import coerce_uuid


def build_customer_query(
    db: Session,
    organization_id: str,
    search: str | None = None,
    status: str | None = None,
) -> Query:
    """
    Build the base AR customer query with filters applied.
    """
    org_id = coerce_uuid(organization_id)

    is_active = None
    if status == "active":
        is_active = True
    elif status == "inactive":
        is_active = False

    query = Query([Customer], session=db).filter(Customer.organization_id == org_id)

    if is_active is not None:
        query = query.filter(Customer.is_active == is_active)
    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            (Customer.customer_code.ilike(search_pattern))
            | (Customer.legal_name.ilike(search_pattern))
            | (Customer.trading_name.ilike(search_pattern))
            | (Customer.tax_identification_number.ilike(search_pattern))
        )

    return query
