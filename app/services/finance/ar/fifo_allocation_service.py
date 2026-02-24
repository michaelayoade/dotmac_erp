"""
FIFO Auto-Allocation Service for Splynx Payments.

Allocates unmatched Splynx payments to invoices using First-In-First-Out
(oldest payment -> oldest unpaid invoice).  Creates PaymentAllocation
records without modifying invoice.amount_paid or invoice.status — Splynx
sync is the source of truth for those fields.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.finance.ar.payment_allocation import PaymentAllocation

logger = logging.getLogger(__name__)

# Ignore balances below this threshold (sub-cent rounding dust)
DUST_THRESHOLD = Decimal("0.01")


@dataclass
class AllocationResult:
    """Summary of a FIFO allocation run."""

    customers_processed: int = 0
    allocations_created: int = 0
    total_allocated: Decimal = field(default_factory=lambda: Decimal("0"))
    prepayment_customers: list[tuple[UUID, str, Decimal]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ------------------------------------------------------------------
# Internal row types returned by the loading queries
# ------------------------------------------------------------------


@dataclass
class _PaymentRow:
    payment_id: UUID
    customer_id: UUID
    customer_name: str
    payment_date: str  # kept as string from DB; only used for sorting
    amount: Decimal
    already_allocated: Decimal

    @property
    def remaining(self) -> Decimal:
        return self.amount - self.already_allocated


@dataclass
class _InvoiceRow:
    invoice_id: UUID
    customer_id: UUID
    invoice_date: str
    total_amount: Decimal
    already_allocated: Decimal

    @property
    def remaining(self) -> Decimal:
        return self.total_amount - self.already_allocated


# ------------------------------------------------------------------
# Service
# ------------------------------------------------------------------


class FIFOAllocationService:
    """FIFO allocation of Splynx payments to invoices."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ----------------------------------------------------------
    # Public API
    # ----------------------------------------------------------

    def allocate_for_org(
        self,
        organization_id: UUID,
        *,
        dry_run: bool = True,
    ) -> AllocationResult:
        """Run FIFO allocation for every customer in *organization_id*.

        When *dry_run* is ``True`` (default) no records are written.
        """
        result = AllocationResult()

        payments = self._load_unallocated_payments(organization_id)
        invoices = self._load_unallocated_invoices(organization_id)

        if not payments:
            logger.info("No unallocated Splynx payments found")
            return result

        # Pre-load existing (payment_id, invoice_id) pairs so we never
        # create a duplicate that would violate the uq_allocation constraint.
        existing_pairs = self._load_existing_pairs(organization_id)

        # Group by customer ------------------------------------------------
        payments_by_cust: dict[UUID, list[_PaymentRow]] = {}
        for p in payments:
            payments_by_cust.setdefault(p.customer_id, []).append(p)

        invoices_by_cust: dict[UUID, list[_InvoiceRow]] = {}
        for inv in invoices:
            invoices_by_cust.setdefault(inv.customer_id, []).append(inv)

        # Process each customer with payments ------------------------------
        for customer_id in sorted(payments_by_cust):
            cust_payments = payments_by_cust[customer_id]
            cust_invoices = invoices_by_cust.get(customer_id, [])
            result.customers_processed += 1

            try:
                self._allocate_customer(
                    cust_payments,
                    cust_invoices,
                    existing_pairs,
                    result,
                    dry_run=dry_run,
                )
            except Exception as exc:
                name = cust_payments[0].customer_name
                logger.exception(
                    "FIFO error for customer %s (%s): %s",
                    name,
                    customer_id,
                    exc,
                )
                result.errors.append(f"Customer {name} ({customer_id}): {exc}")

        if not dry_run:
            self.db.flush()

        return result

    # ----------------------------------------------------------
    # Per-customer FIFO walk
    # ----------------------------------------------------------

    def _allocate_customer(
        self,
        payments: list[_PaymentRow],
        invoices: list[_InvoiceRow],
        existing_pairs: set[tuple[UUID, UUID]],
        result: AllocationResult,
        *,
        dry_run: bool,
    ) -> None:
        """Two-pointer FIFO walk for a single customer."""
        # Sort FIFO: oldest first, tie-break on UUID for determinism
        payments.sort(key=lambda p: (p.payment_date, str(p.payment_id)))
        invoices.sort(key=lambda i: (i.invoice_date, str(i.invoice_id)))

        # Mutable remaining balance per item
        pay_rem = [p.remaining for p in payments]
        inv_rem = [i.remaining for i in invoices]

        pi = 0  # payment pointer
        ii = 0  # invoice pointer

        while pi < len(payments) and ii < len(invoices):
            # Skip exhausted items
            if pay_rem[pi] < DUST_THRESHOLD:
                pi += 1
                continue
            if inv_rem[ii] < DUST_THRESHOLD:
                ii += 1
                continue

            payment = payments[pi]
            invoice = invoices[ii]

            # Guard against duplicate allocation pairs
            pair = (payment.payment_id, invoice.invoice_id)
            if pair in existing_pairs:
                # Pair already allocated — skip this invoice for this payment
                ii += 1
                continue

            alloc_amount = min(pay_rem[pi], inv_rem[ii])
            if alloc_amount < DUST_THRESHOLD:
                ii += 1
                continue

            # Create the allocation record
            if not dry_run:
                allocation = PaymentAllocation(
                    payment_id=payment.payment_id,
                    invoice_id=invoice.invoice_id,
                    allocated_amount=alloc_amount,
                    allocation_date=payment.payment_date,
                )
                self.db.add(allocation)

            pay_rem[pi] -= alloc_amount
            inv_rem[ii] -= alloc_amount
            result.allocations_created += 1
            result.total_allocated += alloc_amount

        # Detect prepayment customers (payment balance after all invoices)
        customer_excess = sum((r for r in pay_rem if r >= DUST_THRESHOLD), Decimal("0"))
        if customer_excess >= DUST_THRESHOLD:
            name = payments[0].customer_name
            customer_id = payments[0].customer_id
            result.prepayment_customers.append((customer_id, name, customer_excess))

    # ----------------------------------------------------------
    # Data loading helpers (raw SQL for batch efficiency)
    # ----------------------------------------------------------

    def _load_unallocated_payments(self, organization_id: UUID) -> list[_PaymentRow]:
        """Load CLEARED Splynx payments with remaining unallocated balance."""
        rows = self.db.execute(
            text("""
                SELECT cp.payment_id,
                       cp.customer_id,
                       c.legal_name,
                       cp.payment_date,
                       cp.amount,
                       COALESCE(SUM(pa.allocated_amount), 0) AS already_allocated
                FROM ar.customer_payment cp
                JOIN ar.customer c ON c.customer_id = cp.customer_id
                LEFT JOIN ar.payment_allocation pa
                    ON pa.payment_id = cp.payment_id
                WHERE cp.organization_id = :org_id
                  AND cp.splynx_id IS NOT NULL
                  AND cp.status::text = 'CLEARED'
                  AND cp.amount > 0
                GROUP BY cp.payment_id, cp.customer_id, c.legal_name,
                         cp.payment_date, cp.amount
                HAVING cp.amount - COALESCE(SUM(pa.allocated_amount), 0) > :dust
            """),
            {"org_id": str(organization_id), "dust": float(DUST_THRESHOLD)},
        ).fetchall()

        return [
            _PaymentRow(
                payment_id=r[0],
                customer_id=r[1],
                customer_name=r[2],
                payment_date=r[3],
                amount=Decimal(str(r[4])),
                already_allocated=Decimal(str(r[5])),
            )
            for r in rows
        ]

    def _load_unallocated_invoices(self, organization_id: UUID) -> list[_InvoiceRow]:
        """Load invoices with remaining unallocated balance."""
        rows = self.db.execute(
            text("""
                SELECT i.invoice_id,
                       i.customer_id,
                       i.invoice_date,
                       i.total_amount,
                       COALESCE(SUM(pa.allocated_amount), 0) AS already_allocated
                FROM ar.invoice i
                LEFT JOIN ar.payment_allocation pa
                    ON pa.invoice_id = i.invoice_id
                WHERE i.organization_id = :org_id
                  AND i.invoice_type::text = 'STANDARD'
                  AND i.status::text IN (
                      'POSTED', 'PARTIALLY_PAID', 'PAID', 'OVERDUE'
                  )
                GROUP BY i.invoice_id, i.customer_id,
                         i.invoice_date, i.total_amount
                HAVING i.total_amount - COALESCE(SUM(pa.allocated_amount), 0)
                       > :dust
            """),
            {"org_id": str(organization_id), "dust": float(DUST_THRESHOLD)},
        ).fetchall()

        return [
            _InvoiceRow(
                invoice_id=r[0],
                customer_id=r[1],
                invoice_date=r[2],
                total_amount=Decimal(str(r[3])),
                already_allocated=Decimal(str(r[4])),
            )
            for r in rows
        ]

    def _load_existing_pairs(self, organization_id: UUID) -> set[tuple[UUID, UUID]]:
        """Load all existing (payment_id, invoice_id) allocation pairs."""
        rows = self.db.execute(
            text("""
                SELECT pa.payment_id, pa.invoice_id
                FROM ar.payment_allocation pa
                JOIN ar.customer_payment cp
                    ON cp.payment_id = pa.payment_id
                WHERE cp.organization_id = :org_id
                  AND cp.splynx_id IS NOT NULL
            """),
            {"org_id": str(organization_id)},
        ).fetchall()
        return {(r[0], r[1]) for r in rows}
