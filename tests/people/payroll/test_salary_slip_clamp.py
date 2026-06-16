from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.services.people.payroll.salary_slip_service import SalarySlipService


def _line(amount: str) -> SimpleNamespace:
    """A deduction-line-like object whose .amount can be mutated in place."""
    return SimpleNamespace(amount=Decimal(amount))


def _slip() -> SimpleNamespace:
    return SimpleNamespace(slip_id=uuid4(), slip_number="SLIP-2026-00001")


def _mock_db(rows: list[tuple]) -> MagicMock:
    """db.execute(select(...)).all() returns (line, component_code) tuples."""
    db = MagicMock()
    db.execute.return_value.all.return_value = rows
    return db


# ── F35: clamp deductions so they never exceed gross ────────────────────────


def test_clamp_no_op_when_deductions_below_gross():
    db = MagicMock()
    slip = _slip()
    total, net = SalarySlipService._clamp_deductions_to_gross(
        db, slip, gross_pay=Decimal("1000"), total_deduction=Decimal("300")
    )
    assert total == Decimal("300")
    assert net == Decimal("700")
    # No clamping path: no flush / no line query needed.
    db.flush.assert_not_called()
    db.execute.assert_not_called()


def test_clamp_exact_match_yields_zero_net_without_scaling():
    db = MagicMock()
    slip = _slip()
    total, net = SalarySlipService._clamp_deductions_to_gross(
        db, slip, gross_pay=Decimal("1000"), total_deduction=Decimal("1000")
    )
    assert total == Decimal("1000")
    assert net == Decimal("0")
    db.execute.assert_not_called()


def test_clamp_scales_down_non_statutory_first():
    """Gross 1000; statutory PAYE 200 + non-statutory loan 900 = 1100 deductions.
    Excess 100 must come entirely out of the non-statutory line, leaving the
    statutory deduction intact, total == gross, net == 0."""
    slip = _slip()
    paye = _line("200")  # statutory
    loan = _line("900")  # non-statutory
    db = _mock_db([(paye, "PAYE"), (loan, "LOAN_REPAYMENT")])

    total, net = SalarySlipService._clamp_deductions_to_gross(
        db, slip, gross_pay=Decimal("1000"), total_deduction=Decimal("1100")
    )

    assert net == Decimal("0")
    assert total == Decimal("1000")
    assert paye.amount == Decimal("200")  # statutory untouched
    assert loan.amount == Decimal("800")  # absorbed the 100 excess
    # Lines now sum to gross so the GL journal balances.
    assert paye.amount + loan.amount == Decimal("1000")
    db.flush.assert_called_once()


def test_clamp_scales_statutory_only_as_last_resort():
    """Statutory withholding alone (1200) exceeds gross (1000) and there is no
    non-statutory deduction; the statutory lines must be scaled so they sum to
    gross and the journal stays balanced."""
    slip = _slip()
    paye = _line("800")
    pension = _line("400")
    db = _mock_db([(paye, "PAYE"), (pension, "PENSION")])

    total, net = SalarySlipService._clamp_deductions_to_gross(
        db, slip, gross_pay=Decimal("1000"), total_deduction=Decimal("1200")
    )

    assert net == Decimal("0")
    assert total == Decimal("1000")
    assert paye.amount + pension.amount == Decimal("1000")


def test_clamp_multiple_non_statutory_proportional_with_exact_total():
    """Two non-statutory lines absorb the excess; the last line takes the
    rounding remainder so the reduced lines sum exactly to gross."""
    slip = _slip()
    a = _line("333.33")
    b = _line("666.67")  # total 1000
    paye = _line("100")  # statutory, untouched
    db = _mock_db([(paye, "PAYE"), (a, "UNION_DUES"), (b, "LOAN_REPAYMENT")])

    total, net = SalarySlipService._clamp_deductions_to_gross(
        db, slip, gross_pay=Decimal("1000"), total_deduction=Decimal("1100")
    )

    assert net == Decimal("0")
    assert total == Decimal("1000")
    assert paye.amount == Decimal("100")
    # Non-statutory lines reduced by a combined 100, summing to 900.
    assert a.amount + b.amount == Decimal("900")
    assert paye.amount + a.amount + b.amount == Decimal("1000")
