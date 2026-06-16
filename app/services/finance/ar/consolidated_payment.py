"""
Consolidated (reseller) payments.

A reseller/parent account can settle one lump payment that is auto-allocated
across the open invoices of its whole account family (parent + sub-accounts),
oldest due-date first. The payment is recorded against the parent; the existing
``CustomerPaymentService.create_payment`` (with ``consolidated=True``) validates
family membership, enforces a shared AR control account, and posts the usual
balanced journal — so this service only owns the FIFO allocation maths.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.ar.invoice import Invoice, InvoiceStatus
from app.services.finance.ar.customer_family import CustomerFamilyResolver
from app.services.finance.ar.customer_payment import (
    CustomerPaymentInput,
    PaymentAllocationInput,
    PaymentMethod,
)

logger = logging.getLogger(__name__)

_OPEN_STATUSES = [
    InvoiceStatus.POSTED,
    InvoiceStatus.PARTIALLY_PAID,
    InvoiceStatus.OVERDUE,
]


class ConsolidatedPaymentService:
    """Build a family-wide FIFO allocation for a reseller payment."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def family_open_invoices(self, org_id: UUID, parent_id: UUID) -> list[Invoice]:
        """Open invoices across the account family, oldest due-date first."""
        family_ids = CustomerFamilyResolver(self.db).family_ids(org_id, parent_id)
        return list(
            self.db.scalars(
                select(Invoice)
                .where(
                    Invoice.organization_id == org_id,
                    Invoice.customer_id.in_(family_ids),
                    Invoice.status.in_(_OPEN_STATUSES),
                )
                .order_by(Invoice.due_date.asc(), Invoice.invoice_date.asc())
            ).all()
        )

    @staticmethod
    def build_fifo_allocations(
        invoices: list[Invoice], amount: Decimal
    ) -> list[PaymentAllocationInput]:
        """
        Spread ``amount`` over ``invoices`` oldest-first, capping each allocation
        at the invoice's balance due. Any remainder is left unallocated (an
        advance/credit on the account).
        """
        remaining = amount
        allocations: list[PaymentAllocationInput] = []
        for inv in invoices:
            if remaining <= 0:
                break
            due = inv.balance_due
            if due <= 0:
                continue
            take = min(due, remaining)
            allocations.append(
                PaymentAllocationInput(invoice_id=inv.invoice_id, amount=take)
            )
            remaining -= take
        return allocations

    def build_consolidated_input(
        self,
        org_id: UUID,
        parent_id: UUID,
        *,
        amount: Decimal,
        payment_date: date,
        payment_method: PaymentMethod,
        bank_account_id: UUID,
        currency_code: str,
        reference: str | None = None,
        description: str | None = None,
    ) -> CustomerPaymentInput:
        """Build a ``consolidated=True`` payment input with FIFO family allocations."""
        invoices = self.family_open_invoices(org_id, parent_id)
        allocations = self.build_fifo_allocations(invoices, amount)
        allocated = sum((a.amount for a in allocations), Decimal("0"))
        logger.info(
            "Consolidated payment for %s: allocating %s of %s across %d invoice(s)",
            parent_id,
            allocated,
            amount,
            len(allocations),
        )
        return CustomerPaymentInput(
            customer_id=parent_id,
            payment_date=payment_date,
            payment_method=payment_method,
            currency_code=currency_code,
            amount=amount,
            bank_account_id=bank_account_id,
            allocations=allocations,
            reference=reference,
            description=description,
            consolidated=True,
        )
