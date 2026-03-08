"""
Shared AP supplier query builder for list + export.
"""

from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models.finance.ap.supplier import Supplier
from app.services.common import coerce_uuid


def build_supplier_query(
    db: Session,
    organization_id: str,
    search: str | None = None,
    status: str | None = None,
) -> Select:
    """
    Build the base AP supplier query with filters applied.

    Returns a SQLAlchemy 2.0 Select statement (not a legacy Query).
    """
    org_id = coerce_uuid(organization_id)

    is_active = None
    if status == "active":
        is_active = True
    elif status == "inactive":
        is_active = False

    stmt = select(Supplier).where(Supplier.organization_id == org_id)

    if is_active is not None:
        stmt = stmt.where(Supplier.is_active == is_active)
    if search:
        search_pattern = f"%{search}%"
        stmt = stmt.where(
            (Supplier.supplier_code.ilike(search_pattern))
            | (Supplier.legal_name.ilike(search_pattern))
            | (Supplier.trading_name.ilike(search_pattern))
            | (Supplier.tax_identification_number.ilike(search_pattern))
        )

    return stmt
