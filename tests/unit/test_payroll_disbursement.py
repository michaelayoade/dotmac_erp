"""A salary slip records how much was disbursed, not just that it was.

ADR-0016 stage 2 (expand). Before this the slip had `paid_at`, `paid_by_id`
and `payment_reference` — who and when, never how much — so disbursing ₦50,000
against a ₦100,000 slip left it reading PAID with no column to disagree.

These pin the two properties that matter: an explicit amount is recorded
faithfully, and an omitted one records the full `net_pay` — the claim marking a
slip PAID has always made implicitly, now written down where a query can find
it.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.services.finance.coverage import PaymentCoverage, coverage_of
from app.services.people.payroll.disbursement import record_disbursement


def _slip(net_pay: str) -> SimpleNamespace:
    """A stand-in whose only relevant surface is the two amounts.

    Deliberately not a real `SalarySlip`: `balance_due` is a generated column
    with no value until flush, so a model instance here would test SQLAlchemy
    rather than this rule.
    """
    return SimpleNamespace(net_pay=Decimal(net_pay), amount_paid=Decimal("0"))


def test_an_explicit_amount_is_recorded() -> None:
    """The capacity that did not exist at all before."""
    slip = _slip("100000.00")
    written = record_disbursement(slip, Decimal("50000.00"))
    assert slip.amount_paid == Decimal("50000.00")
    assert written == Decimal("50000.00")


def test_a_part_disbursement_no_longer_reads_as_settled() -> None:
    """The defect, stated as a test. Paying half a slip now derives PARTIAL —
    which the schema could not express at all until this change, since there
    was no `amount_paid` and `SalarySlipStatus` has no PARTIALLY_PAID."""
    slip = _slip("100000.00")
    record_disbursement(slip, Decimal("50000.00"))

    assert (
        coverage_of(total_amount=slip.net_pay, amount_paid=slip.amount_paid)
        is PaymentCoverage.PARTIAL
    )


def test_omitting_the_amount_records_the_full_net_pay() -> None:
    """Every existing caller omits it, so this is the migration-safe default.

    It is NOT the defect returning: marking a slip PAID has always asserted
    settlement in full. Writing `net_pay` makes that same assertion explicit
    and queryable rather than implicit and invisible. Leaving zero would assert
    the opposite of what the caller meant and make every paid slip read UNPAID.
    """
    slip = _slip("100000.00")
    record_disbursement(slip)

    assert slip.amount_paid == Decimal("100000.00")
    assert (
        coverage_of(total_amount=slip.net_pay, amount_paid=slip.amount_paid)
        is PaymentCoverage.PAID
    )


def test_zero_is_not_treated_as_omitted() -> None:
    """`Decimal("0")` is falsy. A caller recording that nothing moved must not
    be given the full `net_pay` by a truthiness check — that would turn "the
    transfer failed" into "paid in full", which is the worst available bug."""
    slip = _slip("100000.00")
    record_disbursement(slip, Decimal("0"))

    assert slip.amount_paid == Decimal("0")
    assert (
        coverage_of(total_amount=slip.net_pay, amount_paid=slip.amount_paid)
        is PaymentCoverage.UNPAID
    )


def test_an_overpayment_is_recorded_rather_than_clamped() -> None:
    """Paying more than the slip is a real event — a duplicate transfer, a
    correction — and the ledger must be able to say so. Silently clamping to
    `net_pay` would hide exactly the case someone needs to find."""
    slip = _slip("100000.00")
    record_disbursement(slip, Decimal("150000.00"))

    assert slip.amount_paid == Decimal("150000.00")
    assert (
        coverage_of(total_amount=slip.net_pay, amount_paid=slip.amount_paid)
        is PaymentCoverage.OVERPAID
    )
