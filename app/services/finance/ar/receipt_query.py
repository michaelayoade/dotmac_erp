"""
Shared AR receipt query builder for list + export.
"""

from __future__ import annotations

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.models.finance.ar.customer import Customer
from app.models.finance.ar.customer_payment import CustomerPayment
from app.services.common import coerce_uuid
from app.services.finance.ar.web.base import parse_receipt_status
from app.services.finance.common import parse_date


def build_receipt_query(
    db: Session,
    organization_id: str,
    search: str | None = None,
    customer_id: str | None = None,
    status: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> Select:
    """
    Build the base AR receipt query with filters applied.
    """
    org_id = coerce_uuid(organization_id)
    status_value = parse_receipt_status(status)
    from_date = parse_date(start_date)
    to_date = parse_date(end_date)

    query = (
        select(CustomerPayment)
        .join(Customer, CustomerPayment.customer_id == Customer.customer_id)
        .where(CustomerPayment.organization_id == org_id)
    )

    if customer_id:
        query = query.where(CustomerPayment.customer_id == coerce_uuid(customer_id))
    if status_value:
        query = query.where(CustomerPayment.status == status_value)
    if from_date:
        query = query.where(CustomerPayment.payment_date >= from_date)
    if to_date:
        query = query.where(CustomerPayment.payment_date <= to_date)
    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            or_(
                CustomerPayment.payment_number.ilike(search_pattern),
                CustomerPayment.reference.ilike(search_pattern),
                CustomerPayment.description.ilike(search_pattern),
            )
        )

    return query
