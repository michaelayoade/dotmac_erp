"""
Shared AR invoice query builder for list + export.
"""

from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Query, Session

from app.models.finance.ar.customer import Customer
from app.models.finance.ar.invoice import Invoice
from app.services.common import coerce_uuid
from app.services.finance.ar.web.base import parse_invoice_status
from app.services.finance.common import parse_date


def build_invoice_query(
    db: Session,
    organization_id: str,
    search: str | None = None,
    customer_id: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> Query:
    """
    Build the base AR invoice query with filters applied.

    This is the single source of truth for list and export filtering.
    """
    org_id = coerce_uuid(organization_id)
    status_value = parse_invoice_status(status)
    from_date = parse_date(start_date)
    to_date = parse_date(end_date)

    query = (
        db.query(Invoice)
        .join(Customer, Invoice.customer_id == Customer.customer_id)
        .filter(Invoice.organization_id == org_id)
    )

    if customer_id:
        query = query.filter(Invoice.customer_id == coerce_uuid(customer_id))
    if status_value:
        query = query.filter(Invoice.status == status_value)
    if from_date:
        query = query.filter(Invoice.invoice_date >= from_date)
    if to_date:
        query = query.filter(Invoice.invoice_date <= to_date)
    if search:
        search_pattern = f"%{search}%"
        query = query.filter(
            or_(
                Invoice.invoice_number.ilike(search_pattern),
                Customer.legal_name.ilike(search_pattern),
                Customer.trading_name.ilike(search_pattern),
            )
        )

    return query
