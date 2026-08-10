"""
Advance Payment Allocation Service.

When an invoice is posted, automatically applies any unallocated (advance)
receipts from the same customer using FIFO ordering (oldest receipt first).

Creates PaymentAllocation records and updates invoice.amount_paid / status.
No additional GL journal is needed because AR receipts already credit the
customer's AR Control account at posting time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.ar.customer_payment import CustomerPayment, PaymentStatus
from app.models.finance.ar.invoice import Invoice, InvoiceStatus
from app.models.finance.ar.payment_allocation import PaymentAllocation
from app.services.finance.ar.payment_status import (
    PAYMENT_DUST,
    apply_payment_status,
)

logger = logging.getLogger(__name__)

# Sub-cent rounding dust — ignore balances below this. Aliased to the shared
# threshold so "too small to allocate" and "small enough to count as paid"
# cannot drift apart; they were already the same number, declared twice.
DUST_THRESHOLD = PAYMENT_DUST


@dataclass
class AdvanceAllocationResult:
    """Summary returned after applying advances to a single invoice."""

    allocations_created: int = 0
    total_applied: Decimal = field(default_factory=lambda: Decimal("0"))
    invoice_fully_paid: bool = False


class AdvanceAllocationService:
    """Apply unallocated customer receipts against a newly posted invoice."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def apply_to_invoice(self, invoice: Invoice) -> AdvanceAllocationResult:
        """Find unallocated CLEARED receipts for the invoice's customer and
        apply them FIFO until the invoice balance is zero or receipts are
        exhausted.

        Call this **after** the invoice has been posted (status = POSTED)
        so the GL debit already exists.

        Args:
            invoice: A freshly-posted invoice with ``balance_due > 0``.

        Returns:
            Summary of allocations created.
        """
        result = AdvanceAllocationResult()

        if invoice.balance_due <= DUST_THRESHOLD:
            return result

        receipts = self._get_unallocated_receipts(
            invoice.organization_id,
            invoice.customer_id,
        )

        if not receipts:
            return result

        remaining_balance = invoice.balance_due

        for payment, unallocated in receipts:
            if remaining_balance <= DUST_THRESHOLD:
                break

            alloc_amount = min(remaining_balance, unallocated)
            if alloc_amount < DUST_THRESHOLD:
                continue

            allocation = PaymentAllocation(
                payment_id=payment.payment_id,
                invoice_id=invoice.invoice_id,
                allocated_amount=alloc_amount,
                allocation_date=date.today(),
            )
            self.db.add(allocation)

            # Update invoice running totals
            invoice.amount_paid += alloc_amount
            remaining_balance -= alloc_amount

            result.allocations_created += 1
            result.total_applied += alloc_amount

            logger.info(
                "Auto-allocated ₦%s from receipt %s to invoice %s",
                alloc_amount,
                payment.payment_number,
                invoice.invoice_number,
            )

        # Update invoice status based on new balance
        result.invoice_fully_paid = apply_payment_status(invoice) is InvoiceStatus.PAID

        self.db.flush()

        if result.allocations_created:
            logger.info(
                "Advance allocation: %d receipt(s) applied ₦%s to %s — %s",
                result.allocations_created,
                result.total_applied,
                invoice.invoice_number,
                "PAID" if result.invoice_fully_paid else "PARTIALLY_PAID",
            )

        return result

    def _get_unallocated_receipts(
        self,
        organization_id: UUID,
        customer_id: UUID,
    ) -> list[tuple[CustomerPayment, Decimal]]:
        """Return CLEARED receipts with unallocated balance, oldest first.

        Returns:
            List of ``(payment, unallocated_amount)`` tuples sorted by
            payment_date ASC (FIFO).
        """
        # Subquery: total allocated per payment
        alloc_sum = (
            select(
                PaymentAllocation.payment_id,
                func.coalesce(
                    func.sum(PaymentAllocation.allocated_amount), Decimal("0")
                ).label("total_allocated"),
            )
            .group_by(PaymentAllocation.payment_id)
            .subquery()
        )

        stmt = (
            select(
                CustomerPayment,
                (
                    CustomerPayment.gross_amount
                    - func.coalesce(alloc_sum.c.total_allocated, Decimal("0"))
                ).label("unallocated"),
            )
            .outerjoin(
                alloc_sum,
                alloc_sum.c.payment_id == CustomerPayment.payment_id,
            )
            .where(
                CustomerPayment.organization_id == organization_id,
                CustomerPayment.customer_id == customer_id,
                CustomerPayment.status == PaymentStatus.CLEARED,
                CustomerPayment.gross_amount > Decimal("0"),
            )
            .having(
                (
                    CustomerPayment.gross_amount
                    - func.coalesce(alloc_sum.c.total_allocated, Decimal("0"))
                )
                > DUST_THRESHOLD
            )
            .group_by(CustomerPayment.payment_id, alloc_sum.c.total_allocated)
            .order_by(CustomerPayment.payment_date.asc(), CustomerPayment.payment_id)
        )

        rows = self.db.execute(stmt).all()
        return [(row[0], Decimal(str(row[1]))) for row in rows]
