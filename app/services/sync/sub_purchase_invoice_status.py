"""Authoritative ERP read model for Sub-originated supplier invoices.

ERP owns accounts-payable settlement.  This service exposes that state as a
transport-neutral observation keyed by Sub's source invoice ID; HTTP routes,
jobs, and other adapters translate its result without leaking transport types
into the owner.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.ap.supplier_invoice import SupplierInvoice


class PurchaseInvoiceStatusNotFoundError(LookupError):
    """No Sub-originated supplier invoice exists for this organization."""

    code = "purchase_invoice_status_not_found"

    def __init__(self, source_invoice_id: UUID) -> None:
        self.source_invoice_id = source_invoice_id
        super().__init__(f"Purchase invoice not found: {source_invoice_id}")


@dataclass(frozen=True, slots=True)
class PurchaseInvoiceStatusObservation:
    """Current ERP-owned AP settlement state for one Sub source invoice."""

    source_invoice_id: UUID
    purchase_invoice_id: UUID
    invoice_number: str
    status: str
    currency: str
    total_amount: Decimal
    amount_paid: Decimal
    balance_due: Decimal
    source_updated_at: datetime | None


def get_purchase_invoice_status(
    db: Session,
    *,
    organization_id: UUID,
    source_invoice_id: UUID,
) -> PurchaseInvoiceStatusObservation:
    """Read a Sub-originated supplier invoice within its tenant boundary."""
    correlation_id = f"sub-invoice:{source_invoice_id}"
    invoice = db.scalar(
        select(SupplierInvoice).where(
            SupplierInvoice.organization_id == organization_id,
            SupplierInvoice.correlation_id == correlation_id,
        )
    )
    if invoice is None:
        raise PurchaseInvoiceStatusNotFoundError(source_invoice_id)

    total_amount = Decimal(invoice.total_amount)
    amount_paid = Decimal(invoice.amount_paid)
    return PurchaseInvoiceStatusObservation(
        source_invoice_id=source_invoice_id,
        purchase_invoice_id=invoice.invoice_id,
        invoice_number=invoice.invoice_number,
        status=invoice.status.value.lower(),
        currency=invoice.currency_code,
        total_amount=total_amount,
        amount_paid=amount_paid,
        balance_due=total_amount - amount_paid,
        source_updated_at=invoice.updated_at,
    )
