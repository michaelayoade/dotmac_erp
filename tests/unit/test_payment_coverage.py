"""The coverage vocabulary and its one derivation.

ADR-0016 stage 2. These pin the boundaries, because the boundaries are where
the twelve divergent rules disagreed: three different tolerances, four
different nothing-paid answers, and an invoice one kobo short reading PAID down
one path and PARTIALLY_PAID down another.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.finance.coverage import (
    PAYMENT_DUST_DEFAULT,
    PaymentCoverage,
    coverage_of,
)


def _coverage(total: str, paid: str, dust: str | None = None) -> PaymentCoverage:
    return coverage_of(
        total_amount=Decimal(total),
        amount_paid=Decimal(paid),
        dust=Decimal(dust) if dust is not None else PAYMENT_DUST_DEFAULT,
    )


# --------------------------------------------------------------------------
# The four members
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("total", "paid", "expected"),
    [
        ("100.00", "0.00", PaymentCoverage.UNPAID),
        ("100.00", "40.00", PaymentCoverage.PARTIAL),
        ("100.00", "100.00", PaymentCoverage.PAID),
        ("100.00", "150.00", PaymentCoverage.OVERPAID),
    ],
)
def test_the_ordinary_cases(total: str, paid: str, expected) -> None:
    assert _coverage(total, paid) is expected


def test_the_vocabulary_is_exactly_four_members() -> None:
    """Closed on purpose — ADR-0008 says vocabularies are open when their
    members belong to modules, and this is the opposite case: these four
    exhaust the arithmetic, so no product can need a fifth."""
    assert [c.value for c in PaymentCoverage] == [
        "unpaid",
        "partial",
        "paid",
        "overpaid",
    ]


# --------------------------------------------------------------------------
# The boundaries — where the old rules disagreed
# --------------------------------------------------------------------------


def test_one_kobo_short_is_paid() -> None:
    """The case that produced the divergence. A balance at or under the dust
    threshold is not a debt; an exact `>=` left these stuck at PARTIALLY_PAID
    over a rounding residue."""
    assert _coverage("100.00", "99.99") is PaymentCoverage.PAID


def test_the_dust_boundary_is_inclusive_on_the_paid_side() -> None:
    """Exactly `dust` outstanding is PAID; a hair more is PARTIAL. Pinned
    because "at or under" versus "under" is precisely the kind of off-by-one
    that let two call sites disagree."""
    assert _coverage("100.00", "99.99") is PaymentCoverage.PAID  # balance == dust
    assert _coverage("100.00", "99.98") is PaymentCoverage.PARTIAL  # balance > dust


def test_a_trivial_payment_is_not_partial() -> None:
    """Symmetry with the paid side: dust paid against a real balance is not a
    part-payment. Without this an accidental ₦0.01 credit would move an
    untouched invoice out of UNPAID and out of the dunning queue."""
    assert _coverage("100.00", "0.01") is PaymentCoverage.UNPAID
    assert _coverage("100.00", "0.02") is PaymentCoverage.PARTIAL


def test_a_trivial_overpayment_is_paid_not_overpaid() -> None:
    """The dust tolerance applies in both directions, or a ₦0.01 rounding
    surplus would report as an overpayment needing a refund."""
    assert _coverage("100.00", "100.01") is PaymentCoverage.PAID
    assert _coverage("100.00", "100.02") is PaymentCoverage.OVERPAID


# --------------------------------------------------------------------------
# The degenerate document
# --------------------------------------------------------------------------


def test_a_zero_total_document_reads_paid() -> None:
    """Nothing is outstanding, so the arithmetic says PAID — and that is now
    ALL it says.

    Under the old scheme this was a bug, because `PAID` had to mean both
    "settled" and "a terminal lifecycle state" at once, so a zero-total
    document claimed a payment that never happened. With the two facts
    separated, coverage reports the balance and lifecycle reports the process;
    aging and dunning key on the balance and are right to skip it.
    """
    assert _coverage("0.00", "0.00") is PaymentCoverage.PAID


def test_a_credit_document_is_overpaid_not_partial() -> None:
    """A negative total with nothing paid: the balance is negative, so it is
    OVERPAID rather than falling through to UNPAID. Pinned because credit
    notes are exactly the documents that reach this function with a sign the
    invoice paths never see."""
    assert _coverage("-100.00", "0.00") is PaymentCoverage.OVERPAID


# --------------------------------------------------------------------------
# The tolerance is a parameter, not a constant
# --------------------------------------------------------------------------


def test_the_tolerance_changes_the_answer() -> None:
    """ADR-0016 §4: the threshold is business policy, so it is a setting read
    at the point of derivation — not welded into the module, and deliberately
    not into the generated column's DDL, where changing it would mean dropping
    and re-adding the column."""
    assert _coverage("100.00", "99.50") is PaymentCoverage.PARTIAL
    assert _coverage("100.00", "99.50", dust="1.00") is PaymentCoverage.PAID


def test_a_zero_tolerance_is_honoured() -> None:
    """An organization may reasonably demand exactness. With dust at zero the
    boundary tests above must flip, which proves the threshold is genuinely
    doing the work rather than being shadowed by a hardcoded default."""
    assert _coverage("100.00", "99.99", dust="0") is PaymentCoverage.PARTIAL
    assert _coverage("100.00", "100.00", dust="0") is PaymentCoverage.PAID
    assert _coverage("100.00", "100.01", dust="0") is PaymentCoverage.OVERPAID
