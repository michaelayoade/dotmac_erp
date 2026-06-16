"""Tests for consolidated (reseller) payment FIFO allocation."""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock

from app.services.finance.ar.consolidated_payment import ConsolidatedPaymentService


def _inv(balance_due: str):
    m = MagicMock()
    m.invoice_id = uuid.uuid4()
    m.balance_due = Decimal(balance_due)
    return m


class TestFifoAllocation:
    def test_allocates_oldest_first_until_amount_exhausted(self) -> None:
        invoices = [_inv("100"), _inv("100"), _inv("100")]
        allocs = ConsolidatedPaymentService.build_fifo_allocations(
            invoices, Decimal("250")
        )
        assert [a.amount for a in allocs] == [
            Decimal("100"),
            Decimal("100"),
            Decimal("50"),
        ]
        assert sum(a.amount for a in allocs) == Decimal("250")
        # Each allocation targets the right invoice, in order.
        assert [a.invoice_id for a in allocs] == [i.invoice_id for i in invoices]

    def test_caps_each_allocation_at_balance_due(self) -> None:
        invoices = [_inv("30"), _inv("200")]
        allocs = ConsolidatedPaymentService.build_fifo_allocations(
            invoices, Decimal("150")
        )
        assert [a.amount for a in allocs] == [Decimal("30"), Decimal("120")]

    def test_skips_fully_paid_invoices(self) -> None:
        invoices = [_inv("0"), _inv("80")]
        allocs = ConsolidatedPaymentService.build_fifo_allocations(
            invoices, Decimal("80")
        )
        assert len(allocs) == 1
        assert allocs[0].amount == Decimal("80")
        assert allocs[0].invoice_id == invoices[1].invoice_id

    def test_overpayment_leaves_remainder_unallocated(self) -> None:
        # Reseller overpays: only the open balance is allocated; the rest is an
        # advance/credit left on the account (not forced onto an invoice).
        invoices = [_inv("100")]
        allocs = ConsolidatedPaymentService.build_fifo_allocations(
            invoices, Decimal("150")
        )
        assert sum(a.amount for a in allocs) == Decimal("100")

    def test_exact_full_settlement(self) -> None:
        invoices = [_inv("40"), _inv("60")]
        allocs = ConsolidatedPaymentService.build_fifo_allocations(
            invoices, Decimal("100")
        )
        assert sum(a.amount for a in allocs) == Decimal("100")
        assert len(allocs) == 2

    def test_zero_payment_allocates_nothing(self) -> None:
        invoices = [_inv("100")]
        allocs = ConsolidatedPaymentService.build_fifo_allocations(
            invoices, Decimal("0")
        )
        assert allocs == []
