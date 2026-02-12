"""
Payment Metadata Resolver.

Resolves GL journal entries back to their source payments
(CustomerPayment or SupplierPayment) and the associated
customer/supplier counterparty.  Used by reconciliation scoring,
match suggestions, and the reconciliation detail UI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PaymentMetadata:
    """Resolved metadata linking a GL entry to its source payment."""

    source_type: str  # "customer_payment" | "supplier_payment"
    payment_id: UUID
    payment_number: str | None
    counterparty_name: str | None
    counterparty_id: UUID | None
    counterparty_type: str  # "customer" | "supplier"
    invoice_numbers: list[str]


def resolve_payment_metadata(
    db: Session,
    source_document_type: str | None,
    source_document_id: UUID | None,
) -> PaymentMetadata | None:
    """Resolve a single journal entry's source document to payment metadata.

    Returns None if the source is not a customer/supplier payment or
    the referenced record doesn't exist.
    """
    if not source_document_type or not source_document_id:
        return None

    doc_type = source_document_type.lower()

    if doc_type in ("customer_payment", "receipt", "ar_payment"):
        return _resolve_customer_payment(db, source_document_id)
    if doc_type in ("supplier_payment", "ap_payment"):
        return _resolve_supplier_payment(db, source_document_id)

    return None


def resolve_payment_metadata_batch(
    db: Session,
    pairs: list[tuple[str | None, UUID | None]],
) -> dict[UUID, PaymentMetadata]:
    """Batch-resolve multiple (source_document_type, source_document_id) pairs.

    Returns a dict keyed by source_document_id for quick lookup.
    Does 2 queries total (one for AR, one for AP) instead of N+1.
    """
    customer_payment_ids: list[UUID] = []
    supplier_payment_ids: list[UUID] = []

    for doc_type, doc_id in pairs:
        if not doc_type or not doc_id:
            continue
        dt = doc_type.lower()
        if dt in ("customer_payment", "receipt", "ar_payment"):
            customer_payment_ids.append(doc_id)
        elif dt in ("supplier_payment", "ap_payment"):
            supplier_payment_ids.append(doc_id)

    result: dict[UUID, PaymentMetadata] = {}

    if customer_payment_ids:
        result.update(_resolve_customer_payments_batch(db, customer_payment_ids))

    if supplier_payment_ids:
        result.update(_resolve_supplier_payments_batch(db, supplier_payment_ids))

    return result


# ---------------------------------------------------------------------------
# Internal resolvers
# ---------------------------------------------------------------------------


def _resolve_customer_payment(db: Session, payment_id: UUID) -> PaymentMetadata | None:
    from app.models.finance.ar.customer import Customer
    from app.models.finance.ar.customer_payment import CustomerPayment

    stmt = (
        select(CustomerPayment, Customer)
        .outerjoin(Customer, CustomerPayment.customer_id == Customer.customer_id)
        .where(CustomerPayment.payment_id == payment_id)
    )
    row = db.execute(stmt).first()
    if not row:
        return None

    payment, customer = row
    invoice_numbers = _get_customer_payment_invoices(db, payment_id)

    return PaymentMetadata(
        source_type="customer_payment",
        payment_id=payment.payment_id,
        payment_number=getattr(payment, "payment_number", None)
        or getattr(payment, "receipt_number", None),
        counterparty_name=customer.customer_name if customer else None,
        counterparty_id=customer.customer_id if customer else None,
        counterparty_type="customer",
        invoice_numbers=invoice_numbers,
    )


def _resolve_supplier_payment(db: Session, payment_id: UUID) -> PaymentMetadata | None:
    from app.models.finance.ap.supplier import Supplier
    from app.models.finance.ap.supplier_payment import SupplierPayment

    stmt = (
        select(SupplierPayment, Supplier)
        .outerjoin(Supplier, SupplierPayment.supplier_id == Supplier.supplier_id)
        .where(SupplierPayment.payment_id == payment_id)
    )
    row = db.execute(stmt).first()
    if not row:
        return None

    payment, supplier = row

    return PaymentMetadata(
        source_type="supplier_payment",
        payment_id=payment.payment_id,
        payment_number=getattr(payment, "payment_number", None),
        counterparty_name=supplier.supplier_name if supplier else None,
        counterparty_id=supplier.supplier_id if supplier else None,
        counterparty_type="supplier",
        invoice_numbers=[],
    )


def _resolve_customer_payments_batch(
    db: Session, payment_ids: list[UUID]
) -> dict[UUID, PaymentMetadata]:
    from app.models.finance.ar.customer import Customer
    from app.models.finance.ar.customer_payment import CustomerPayment

    stmt = (
        select(CustomerPayment, Customer)
        .outerjoin(Customer, CustomerPayment.customer_id == Customer.customer_id)
        .where(CustomerPayment.payment_id.in_(payment_ids))
    )
    rows = db.execute(stmt).all()

    result: dict[UUID, PaymentMetadata] = {}
    for payment, customer in rows:
        result[payment.payment_id] = PaymentMetadata(
            source_type="customer_payment",
            payment_id=payment.payment_id,
            payment_number=getattr(payment, "payment_number", None)
            or getattr(payment, "receipt_number", None),
            counterparty_name=customer.customer_name if customer else None,
            counterparty_id=customer.customer_id if customer else None,
            counterparty_type="customer",
            invoice_numbers=[],
        )
    return result


def _resolve_supplier_payments_batch(
    db: Session, payment_ids: list[UUID]
) -> dict[UUID, PaymentMetadata]:
    from app.models.finance.ap.supplier import Supplier
    from app.models.finance.ap.supplier_payment import SupplierPayment

    stmt = (
        select(SupplierPayment, Supplier)
        .outerjoin(Supplier, SupplierPayment.supplier_id == Supplier.supplier_id)
        .where(SupplierPayment.payment_id.in_(payment_ids))
    )
    rows = db.execute(stmt).all()

    result: dict[UUID, PaymentMetadata] = {}
    for payment, supplier in rows:
        result[payment.payment_id] = PaymentMetadata(
            source_type="supplier_payment",
            payment_id=payment.payment_id,
            payment_number=getattr(payment, "payment_number", None),
            counterparty_name=supplier.supplier_name if supplier else None,
            counterparty_id=supplier.supplier_id if supplier else None,
            counterparty_type="supplier",
            invoice_numbers=[],
        )
    return result


def _get_customer_payment_invoices(db: Session, payment_id: UUID) -> list[str]:
    """Get invoice numbers allocated to a customer payment."""
    try:
        from app.models.finance.ar.invoice import Invoice
        from app.models.finance.ar.payment_allocation import PaymentAllocation

        stmt = (
            select(Invoice.invoice_number)
            .join(
                PaymentAllocation,
                PaymentAllocation.invoice_id == Invoice.invoice_id,
            )
            .where(PaymentAllocation.payment_id == payment_id)
        )
        numbers = db.scalars(stmt).all()
        return [n for n in numbers if n]
    except Exception:
        logger.debug("Could not resolve invoice numbers for payment %s", payment_id)
        return []
