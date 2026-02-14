"""
Shared AP payment query builder for list + export.
"""

from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Query, Session

from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_payment import APPaymentStatus, SupplierPayment
from app.services.common import coerce_uuid
from app.services.finance.ap.web.base import parse_payment_status
from app.services.finance.common import parse_date


def build_payment_query(
    db: Session,
    organization_id: str,
    search: str | None = None,
    supplier_id: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> Query:
    """
    Build the base AP payment query with filters applied.
    """
    org_id = coerce_uuid(organization_id)
    status_value = parse_payment_status(status)
    from_date = parse_date(start_date)
    to_date = parse_date(end_date)

    query = (
        db.query(SupplierPayment)
        .join(Supplier, SupplierPayment.supplier_id == Supplier.supplier_id)
        .filter(SupplierPayment.organization_id == org_id)
    )

    if supplier_id:
        query = query.filter(SupplierPayment.supplier_id == coerce_uuid(supplier_id))
    if status == "POSTED":
        query = query.filter(
            SupplierPayment.status.in_([APPaymentStatus.SENT, APPaymentStatus.CLEARED])
        )
    elif status_value:
        query = query.filter(SupplierPayment.status == status_value)
    if from_date:
        query = query.filter(SupplierPayment.payment_date >= from_date)
    if to_date:
        query = query.filter(SupplierPayment.payment_date <= to_date)
    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            or_(
                SupplierPayment.payment_number.ilike(search_pattern),
                SupplierPayment.reference.ilike(search_pattern),
            )
        )

    return query
