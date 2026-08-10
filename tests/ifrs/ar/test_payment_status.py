"""The single paid-status rule, and the divergences it resolved.

Each case here corresponds to a place the eight former implementations
disagreed. Two of them are deliberate behaviour changes and are marked as
such — they are the point of the consolidation, not incidental drift.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.finance.ar.invoice import InvoiceStatus
from app.services.finance.ar.payment_status import (
    NOT_PAYMENT_DETERMINED,
    PAYMENT_DUST,
    resolve_payment_status,
)

TODAY = date(2026, 8, 10)
PAST = date(2026, 7, 1)
FUTURE = date(2026, 9, 1)


def resolve(total, paid, status=InvoiceStatus.POSTED, due=None, today=TODAY):
    return resolve_payment_status(
        total_amount=Decimal(total),
        amount_paid=Decimal(paid),
        current_status=status,
        due_date=due,
        today=today,
    )


# --------------------------------------------------------------------------
# Coverage
# --------------------------------------------------------------------------


def test_exact_payment_is_paid():
    assert resolve("100.00", "100.00") is InvoiceStatus.PAID


def test_overpayment_is_paid():
    assert resolve("100.00", "150.00") is InvoiceStatus.PAID


def test_partial_payment_is_partially_paid():
    assert resolve("100.00", "40.00") is InvoiceStatus.PARTIALLY_PAID


# --------------------------------------------------------------------------
# The tolerance — BEHAVIOUR CHANGE
#
# Five of the eight former sites used an exact `paid >= total`, so an invoice
# a single kobo short stayed PARTIALLY_PAID forever. `advance_allocation`
# already called 0.01 "sub-cent rounding dust" and refused to allocate below
# it — while deciding status with an exact compare six lines later.
# --------------------------------------------------------------------------


def test_balance_exactly_at_dust_is_paid():
    assert resolve("100.00", "99.99") is InvoiceStatus.PAID


def test_balance_just_over_dust_is_not_paid():
    assert resolve("100.00", "99.98") is InvoiceStatus.PARTIALLY_PAID


def test_dust_is_the_shared_threshold():
    """Not a private constant: the allocation path aliases this one, so
    'too small to allocate' and 'small enough to be paid' cannot diverge."""
    from app.services.finance.ar.advance_allocation import DUST_THRESHOLD

    assert DUST_THRESHOLD is PAYMENT_DUST


# --------------------------------------------------------------------------
# Nothing paid — BEHAVIOUR CHANGE
#
# The forward paths wrote PARTIALLY_PAID onto invoices with amount_paid == 0.
# Only the two reversal paths knew an uncovered invoice is POSTED, or OVERDUE
# past its due date.
# --------------------------------------------------------------------------


def test_nothing_paid_and_not_yet_due_is_posted():
    assert resolve("100.00", "0", due=FUTURE) is InvoiceStatus.POSTED


def test_nothing_paid_and_past_due_is_overdue():
    assert resolve("100.00", "0", due=PAST) is InvoiceStatus.OVERDUE


def test_nothing_paid_with_no_due_date_is_posted():
    assert resolve("100.00", "0", due=None) is InvoiceStatus.POSTED


def test_dust_sized_payment_counts_as_nothing_paid():
    assert resolve("100.00", str(PAYMENT_DUST), due=PAST) is InvoiceStatus.OVERDUE


def test_due_today_is_not_yet_overdue():
    """The boundary is strict: due date must be in the past."""
    assert resolve("100.00", "0", due=TODAY) is InvoiceStatus.POSTED


# --------------------------------------------------------------------------
# Statuses payment does not determine
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status", sorted(NOT_PAYMENT_DETERMINED, key=lambda s: s.value)
)
def test_non_payment_determined_statuses_are_never_changed(status):
    """Even with numbers that would otherwise say PAID."""
    assert resolve("100.00", "100.00", status=status) is status


def test_a_voided_invoice_is_not_relabelled_paid():
    """The specific trap: VOID invoices typically carry a zero balance, so any
    rule keyed on balance alone reports them as PAID. The sync layer had
    already been bitten by this and left a comment about it."""
    assert resolve("0", "0", status=InvoiceStatus.VOID) is InvoiceStatus.VOID


def test_overdue_is_payment_determined():
    """OVERDUE is NOT in the untouchable set — paying an overdue invoice must
    still move it, which is what the payable-status check always allowed."""
    assert (
        resolve("100.00", "100.00", status=InvoiceStatus.OVERDUE) is InvoiceStatus.PAID
    )
    assert (
        resolve("100.00", "50.00", status=InvoiceStatus.OVERDUE)
        is InvoiceStatus.PARTIALLY_PAID
    )


# --------------------------------------------------------------------------
# Purity
# --------------------------------------------------------------------------


def test_today_is_injected_not_read_from_the_clock():
    """Same invoice, two clocks, two answers — proving no hidden date.today()
    and that a batch cannot straddle midnight inconsistently."""
    args = dict(total="100.00", paid="0", due=date(2026, 8, 9))
    assert resolve(**args, today=date(2026, 8, 10)) is InvoiceStatus.OVERDUE
    assert resolve(**args, today=date(2026, 8, 1)) is InvoiceStatus.POSTED


def test_apply_assigns_and_returns():
    class _Inv:
        total_amount = Decimal("100.00")
        amount_paid = Decimal("100.00")
        status = InvoiceStatus.POSTED
        due_date = None

    from app.services.finance.ar.payment_status import apply_payment_status

    inv = _Inv()
    returned = apply_payment_status(inv, today=TODAY)
    assert returned is InvoiceStatus.PAID
    assert inv.status is InvoiceStatus.PAID
