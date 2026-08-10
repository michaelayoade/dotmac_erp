"""AP's paid-status rule, and how it deliberately differs from AR's.

AP carried the same two defects as AR — no tolerance, and PARTIALLY_PAID
written onto a bill with zero coverage — but it is not the same rule, because
`SupplierInvoiceStatus` has no OVERDUE and a larger protected set.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.finance.ap.supplier_invoice import SupplierInvoiceStatus
from app.services.finance.ap.payment_status import (
    NOT_PAYMENT_DETERMINED,
    PAYMENT_DUST,
    apply_payment_status,
    resolve_payment_status,
)


def resolve(total, paid, status=SupplierInvoiceStatus.POSTED):
    return resolve_payment_status(
        total_amount=Decimal(total),
        amount_paid=Decimal(paid),
        current_status=status,
    )


# --------------------------------------------------------------------------
# Coverage
# --------------------------------------------------------------------------


def test_exact_payment_is_paid():
    assert resolve("100.00", "100.00") is SupplierInvoiceStatus.PAID


def test_overpayment_is_paid():
    assert resolve("100.00", "150.00") is SupplierInvoiceStatus.PAID


def test_partial_payment_is_partially_paid():
    assert resolve("100.00", "40.00") is SupplierInvoiceStatus.PARTIALLY_PAID


# --------------------------------------------------------------------------
# Tolerance — BEHAVIOUR CHANGE
#
# All three former AP sites used an exact `paid >= total`, while
# `supplier_payment.py` already used Decimal("0.01") as a tolerance two hundred
# lines earlier to check an allocation total against the payment amount. Same
# file, same number, applied to one comparison and not the other.
# --------------------------------------------------------------------------


def test_balance_exactly_at_dust_is_paid():
    assert resolve("100.00", "99.99") is SupplierInvoiceStatus.PAID


def test_balance_just_over_dust_is_not_paid():
    assert resolve("100.00", "99.98") is SupplierInvoiceStatus.PARTIALLY_PAID


# --------------------------------------------------------------------------
# Nothing paid — BEHAVIOUR CHANGE
#
# The two forward paths wrote PARTIALLY_PAID whenever the bill was not fully
# covered, including at amount_paid == 0. Only the reversal path knew better.
# --------------------------------------------------------------------------


def test_nothing_paid_is_posted():
    assert resolve("100.00", "0") is SupplierInvoiceStatus.POSTED


def test_dust_sized_payment_counts_as_nothing_paid():
    assert resolve("100.00", str(PAYMENT_DUST)) is SupplierInvoiceStatus.POSTED


# --------------------------------------------------------------------------
# Where AP deliberately differs from AR
# --------------------------------------------------------------------------


def test_ap_has_no_overdue_to_resolve_to():
    """AR resolves an uncovered invoice to OVERDUE past its due date. A supplier
    bill we have not paid is simply POSTED — lateness is tracked by AP aging,
    not by the row's status — and the enum has no OVERDUE to hold it."""
    assert not hasattr(SupplierInvoiceStatus, "OVERDUE")


def test_the_rule_takes_no_clock():
    """Consequence of the above: no due_date, no today. Passing one is a
    TypeError, not a silently ignored argument."""
    with pytest.raises(TypeError):
        resolve_payment_status(
            total_amount=Decimal("100"),
            amount_paid=Decimal("0"),
            current_status=SupplierInvoiceStatus.POSTED,
            today="2026-08-10",
        )


def test_ap_protects_statuses_ar_does_not_have():
    """PENDING_APPROVAL, ON_HOLD and REJECTED exist only in AP."""
    assert {
        SupplierInvoiceStatus.PENDING_APPROVAL,
        SupplierInvoiceStatus.ON_HOLD,
        SupplierInvoiceStatus.REJECTED,
    } <= NOT_PAYMENT_DETERMINED


# --------------------------------------------------------------------------
# Statuses payment does not determine
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status", sorted(NOT_PAYMENT_DETERMINED, key=lambda s: s.value)
)
def test_non_payment_determined_statuses_are_never_changed(status):
    assert resolve("100.00", "100.00", status=status) is status


def test_a_voided_bill_is_not_relabelled_paid():
    assert (
        resolve("0", "0", status=SupplierInvoiceStatus.VOID)
        is SupplierInvoiceStatus.VOID
    )


def test_apply_assigns_and_returns():
    class _Inv:
        total_amount = Decimal("100.00")
        amount_paid = Decimal("100.00")
        status = SupplierInvoiceStatus.POSTED

    inv = _Inv()
    assert apply_payment_status(inv) is SupplierInvoiceStatus.PAID
    assert inv.status is SupplierInvoiceStatus.PAID
