"""Tests for advance payment auto-allocation on invoice posting."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4


from app.models.finance.ar.customer_payment import PaymentStatus
from app.models.finance.ar.invoice import InvoiceStatus
from app.services.finance.ar.advance_allocation import (
    AdvanceAllocationService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ORG_ID = uuid4()
CUSTOMER_ID = uuid4()


def _make_invoice(
    total: str = "100000.00",
    paid: str = "0",
    status: InvoiceStatus = InvoiceStatus.POSTED,
) -> Any:
    return SimpleNamespace(
        invoice_id=uuid4(),
        organization_id=ORG_ID,
        customer_id=CUSTOMER_ID,
        invoice_number="INV-00100",
        total_amount=Decimal(total),
        amount_paid=Decimal(paid),
        balance_due=Decimal(total) - Decimal(paid),
        status=status,
    )


def _make_receipt(
    amount: str = "50000.00",
    number: str = "REC-00050",
) -> SimpleNamespace:
    return SimpleNamespace(
        payment_id=uuid4(),
        organization_id=ORG_ID,
        customer_id=CUSTOMER_ID,
        payment_number=number,
        payment_date=date(2026, 1, 15),
        amount=Decimal(amount),
        status=PaymentStatus.CLEARED,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestApplyToInvoice:
    """Tests for AdvanceAllocationService.apply_to_invoice."""

    def test_no_unallocated_receipts(self) -> None:
        """Invoice stays POSTED when customer has no advance payments."""
        db = MagicMock()
        invoice = _make_invoice()
        svc = AdvanceAllocationService(db)

        with patch.object(svc, "_get_unallocated_receipts", return_value=[]):
            result = svc.apply_to_invoice(invoice)

        assert result.allocations_created == 0
        assert result.total_applied == Decimal("0")
        assert invoice.status == InvoiceStatus.POSTED

    def test_single_receipt_fully_covers_invoice(self) -> None:
        """One advance receipt >= invoice total → invoice becomes PAID."""
        db = MagicMock()
        invoice = _make_invoice(total="50000.00")
        receipt = _make_receipt(amount="80000.00")

        svc = AdvanceAllocationService(db)
        with patch.object(
            svc,
            "_get_unallocated_receipts",
            return_value=[(receipt, Decimal("80000.00"))],
        ):
            result = svc.apply_to_invoice(invoice)

        assert result.allocations_created == 1
        assert result.total_applied == Decimal("50000.00")
        assert result.invoice_fully_paid is True
        assert invoice.status == InvoiceStatus.PAID
        assert invoice.amount_paid == Decimal("50000.00")
        db.add.assert_called_once()

    def test_single_receipt_partially_covers_invoice(self) -> None:
        """One advance receipt < invoice total → PARTIALLY_PAID."""
        db = MagicMock()
        invoice = _make_invoice(total="100000.00")
        receipt = _make_receipt(amount="30000.00")

        svc = AdvanceAllocationService(db)
        with patch.object(
            svc,
            "_get_unallocated_receipts",
            return_value=[(receipt, Decimal("30000.00"))],
        ):
            result = svc.apply_to_invoice(invoice)

        assert result.allocations_created == 1
        assert result.total_applied == Decimal("30000.00")
        assert result.invoice_fully_paid is False
        assert invoice.status == InvoiceStatus.PARTIALLY_PAID
        assert invoice.amount_paid == Decimal("30000.00")

    def test_multiple_receipts_cover_invoice(self) -> None:
        """Two receipts together cover the full invoice amount."""
        db = MagicMock()
        invoice = _make_invoice(total="100000.00")
        r1 = _make_receipt(amount="60000.00", number="REC-001")
        r2 = _make_receipt(amount="70000.00", number="REC-002")

        svc = AdvanceAllocationService(db)
        with patch.object(
            svc,
            "_get_unallocated_receipts",
            return_value=[
                (r1, Decimal("60000.00")),
                (r2, Decimal("70000.00")),
            ],
        ):
            result = svc.apply_to_invoice(invoice)

        assert result.allocations_created == 2
        assert result.total_applied == Decimal("100000.00")
        assert result.invoice_fully_paid is True
        assert invoice.status == InvoiceStatus.PAID
        # First receipt fully consumed (60k), second partially (40k)
        assert invoice.amount_paid == Decimal("100000.00")
        assert db.add.call_count == 2

    def test_multiple_receipts_partial_coverage(self) -> None:
        """Multiple receipts don't fully cover the invoice."""
        db = MagicMock()
        invoice = _make_invoice(total="200000.00")
        r1 = _make_receipt(amount="30000.00", number="REC-001")
        r2 = _make_receipt(amount="50000.00", number="REC-002")

        svc = AdvanceAllocationService(db)
        with patch.object(
            svc,
            "_get_unallocated_receipts",
            return_value=[
                (r1, Decimal("30000.00")),
                (r2, Decimal("50000.00")),
            ],
        ):
            result = svc.apply_to_invoice(invoice)

        assert result.allocations_created == 2
        assert result.total_applied == Decimal("80000.00")
        assert result.invoice_fully_paid is False
        assert invoice.status == InvoiceStatus.PARTIALLY_PAID

    def test_already_paid_invoice_skipped(self) -> None:
        """Invoice with zero balance_due is skipped immediately."""
        db = MagicMock()
        invoice = _make_invoice(total="50000.00", paid="50000.00")
        invoice.balance_due = Decimal("0")

        svc = AdvanceAllocationService(db)
        result = svc.apply_to_invoice(invoice)

        assert result.allocations_created == 0

    def test_dust_threshold_ignored(self) -> None:
        """Sub-cent unallocated amounts are ignored."""
        db = MagicMock()
        invoice = _make_invoice(total="100000.00")
        receipt = _make_receipt(amount="0.005")  # Below dust threshold

        svc = AdvanceAllocationService(db)
        with patch.object(
            svc,
            "_get_unallocated_receipts",
            return_value=[(receipt, Decimal("0.005"))],
        ):
            result = svc.apply_to_invoice(invoice)

        assert result.allocations_created == 0
        db.add.assert_not_called()

    def test_receipt_with_partial_prior_allocation(self) -> None:
        """Receipt that was partially allocated before still has remaining balance."""
        db = MagicMock()
        invoice = _make_invoice(total="40000.00")
        # Receipt of 100k, but 70k already allocated — only 30k available
        receipt = _make_receipt(amount="100000.00")

        svc = AdvanceAllocationService(db)
        with patch.object(
            svc,
            "_get_unallocated_receipts",
            return_value=[(receipt, Decimal("30000.00"))],
        ):
            result = svc.apply_to_invoice(invoice)

        assert result.allocations_created == 1
        assert result.total_applied == Decimal("30000.00")
        assert result.invoice_fully_paid is False
        assert invoice.amount_paid == Decimal("30000.00")

    def test_invoice_with_existing_partial_payment(self) -> None:
        """Invoice already partially paid, advance covers the remaining balance."""
        db = MagicMock()
        invoice = _make_invoice(total="100000.00", paid="60000.00")
        invoice.balance_due = Decimal("40000.00")
        receipt = _make_receipt(amount="50000.00")

        svc = AdvanceAllocationService(db)
        with patch.object(
            svc,
            "_get_unallocated_receipts",
            return_value=[(receipt, Decimal("50000.00"))],
        ):
            result = svc.apply_to_invoice(invoice)

        assert result.allocations_created == 1
        assert result.total_applied == Decimal("40000.00")
        assert result.invoice_fully_paid is True
        assert invoice.amount_paid == Decimal("100000.00")

    def test_flush_called_when_allocations_created(self) -> None:
        """db.flush() is called after allocations are created."""
        db = MagicMock()
        invoice = _make_invoice(total="10000.00")
        receipt = _make_receipt(amount="10000.00")

        svc = AdvanceAllocationService(db)
        with patch.object(
            svc,
            "_get_unallocated_receipts",
            return_value=[(receipt, Decimal("10000.00"))],
        ):
            svc.apply_to_invoice(invoice)

        db.flush.assert_called_once()

    def test_no_flush_when_no_allocations(self) -> None:
        """db.flush() not called when there's nothing to allocate."""
        db = MagicMock()
        invoice = _make_invoice(total="10000.00")

        svc = AdvanceAllocationService(db)
        with patch.object(svc, "_get_unallocated_receipts", return_value=[]):
            svc.apply_to_invoice(invoice)

        db.flush.assert_not_called()


def test_unallocated_receipt_query_uses_gross_ar_settlement() -> None:
    db = MagicMock()
    db.execute.return_value.all.return_value = []

    AdvanceAllocationService(db)._get_unallocated_receipts(ORG_ID, CUSTOMER_ID)

    statement = str(db.execute.call_args.args[0])
    assert "gross_amount" in statement
