"""
Shared AP supplier query builder for list + export.
"""

from __future__ import annotations

from sqlalchemy.orm import Query, Session

from app.models.finance.ap.supplier import Supplier
from app.services.common import coerce_uuid


def build_supplier_query(
    db: Session,
    organization_id: str,
    search: str | None = None,
    status: str | None = None,
) -> Query:
    """
    Build the base AP supplier query with filters applied.
    """
    org_id = coerce_uuid(organization_id)

    is_active = None
    if status == "active":
        is_active = True
    elif status == "inactive":
        is_active = False

    query = Query([Supplier], session=db).filter(Supplier.organization_id == org_id)

    if is_active is not None:
        query = query.filter(Supplier.is_active == is_active)
    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            (Supplier.supplier_code.ilike(search_pattern))
            | (Supplier.legal_name.ilike(search_pattern))
            | (Supplier.trading_name.ilike(search_pattern))
            | (Supplier.tax_identification_number.ilike(search_pattern))
        )

    return query
