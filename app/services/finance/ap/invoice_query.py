"""
Shared AP invoice query builder for list + export.
"""

from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Query, Session

from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import SupplierInvoice
from app.services.common import coerce_uuid
from app.services.finance.ap.web.base import parse_invoice_status
from app.services.finance.common import parse_date


def build_invoice_query(
    db: Session,
    organization_id: str,
    search: str | None = None,
    supplier_id: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> Query:
    """
    Build the base AP invoice query with filters applied.

    This is the single source of truth for list and export filtering.
    """
    org_id = coerce_uuid(organization_id)
    status_value = parse_invoice_status(status)
    from_date = parse_date(start_date)
    to_date = parse_date(end_date)

    query: Query[SupplierInvoice] = (
        Query([SupplierInvoice], session=db)
        .join(Supplier, SupplierInvoice.supplier_id == Supplier.supplier_id)
        .filter(SupplierInvoice.organization_id == org_id)
    )

    if supplier_id:
        query = query.filter(SupplierInvoice.supplier_id == coerce_uuid(supplier_id))
    if status_value:
        query = query.filter(SupplierInvoice.status == status_value)
    if from_date:
        query = query.filter(SupplierInvoice.invoice_date >= from_date)
    if to_date:
        query = query.filter(SupplierInvoice.invoice_date <= to_date)
    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            or_(
                SupplierInvoice.invoice_number.ilike(search_pattern),
                SupplierInvoice.supplier_invoice_number.ilike(search_pattern),
                Supplier.legal_name.ilike(search_pattern),
                Supplier.trading_name.ilike(search_pattern),
            )
        )

    return query
