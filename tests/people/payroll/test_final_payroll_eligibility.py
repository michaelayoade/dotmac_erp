from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.models.people.hr.employee import EmployeeStatus
from app.models.people.payroll.salary_slip import SalarySlipStatus
from app.services.people.payroll.eligibility import (
    is_employee_payroll_eligible_for_period,
)
from app.services.people.payroll.payroll_service import PayrollService


def _employee(
    status: EmployeeStatus,
    *,
    leaving: date | None = None,
    eligible: bool = False,
    cutoff: date | None = None,
    processed_at=None,
):
    return SimpleNamespace(
        status=status,
        date_of_leaving=leaving,
        eligible_for_final_payroll=eligible,
        final_payroll_cutoff_date=cutoff,
        final_payroll_processed_at=processed_at,
    )


def test_exited_employee_is_eligible_once_for_matching_period():
    employee = _employee(
        EmployeeStatus.RESIGNED,
        leaving=date(2026, 4, 25),
        eligible=True,
        cutoff=date(2026, 4, 25),
    )

    assert is_employee_payroll_eligible_for_period(
        employee,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )


def test_exited_employee_without_flag_is_not_eligible():
    employee = _employee(
        EmployeeStatus.RESIGNED,
        leaving=date(2026, 4, 25),
        eligible=False,
    )

    assert not is_employee_payroll_eligible_for_period(
        employee,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )


def test_exited_employee_can_run_final_payroll_in_later_period_via_cutoff():
    employee = _employee(
        EmployeeStatus.RESIGNED,
        leaving=date(2026, 3, 25),
        eligible=True,
        cutoff=date(2026, 4, 30),
    )

    assert is_employee_payroll_eligible_for_period(
        employee,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )


def test_processed_final_payroll_employee_is_not_eligible_again():
    employee = _employee(
        EmployeeStatus.TERMINATED,
        leaving=date(2026, 4, 25),
        eligible=True,
        processed_at=datetime(2026, 4, 30, 12, 0, 0),
    )

    assert not is_employee_payroll_eligible_for_period(
        employee,
        period_start=date(2026, 4, 1),
        period_end=date(2026, 4, 30),
    )


def test_payout_clears_final_payroll_flag_and_marks_processed():
    db = SimpleNamespace()
    service = PayrollService(db)
    employee_id = uuid4()
    slip = SimpleNamespace(
        slip_id=uuid4(),
        employee_id=employee_id,
        status=SalarySlipStatus.POSTED,
        paid_at=None,
        paid_by_id=None,
        payment_reference=None,
        employee=None,
        # Paying a slip now records the amount (ADR-0016 expand), so the
        # double needs the two columns coverage derives from.
        net_pay=Decimal("100000.00"),
        amount_paid=Decimal("0"),
    )
    entry = SimpleNamespace(salary_slips=[slip])
    employee = SimpleNamespace(
        eligible_for_final_payroll=True,
        final_payroll_processed_at=None,
    )

    service.get_payroll_entry = lambda _org_id, _entry_id: entry
    db.get = lambda model, pk: employee if pk == employee_id else None
    db.flush = lambda: None

    result = service.payout_payroll_entry(
        uuid4(),
        uuid4(),
        paid_by_id=uuid4(),
        payment_reference="PAY-001",
    )

    assert result["updated"] == 1
    assert employee.eligible_for_final_payroll is False
    assert employee.final_payroll_processed_at is not None
