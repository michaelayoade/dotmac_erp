from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.services.people.payroll.loan_service import LoanService


def _loan(
    *,
    monthly_installment: str = "200.00",
    outstanding_balance: str = "200.00",
    first_repayment_date: date | None = None,
):
    return SimpleNamespace(
        loan_id=uuid4(),
        loan_number="LOAN-2026-00001",
        loan_type=SimpleNamespace(type_name="Salary Advance"),
        monthly_installment=Decimal(monthly_installment),
        outstanding_balance=Decimal(outstanding_balance),
        first_repayment_date=first_repayment_date or date(2026, 1, 1),
        interest_rate=Decimal("0"),
        total_interest=Decimal("0"),
        total_repayable=Decimal(outstanding_balance),
    )


def test_get_due_deductions_skips_existing_period_repayment():
    db = MagicMock()
    svc = LoanService(db)
    svc.get_active_loans_for_employee = MagicMock(return_value=[_loan()])
    svc._has_repayment_in_period = MagicMock(return_value=True)
    svc._has_linked_slip_deduction_in_period = MagicMock(return_value=False)

    result = svc.get_due_deductions(
        employee_id=uuid4(),
        period_start=date(2026, 2, 1),
        period_end=date(2026, 2, 28),
    )

    assert result == []


def test_get_due_deductions_skips_existing_non_cancelled_link():
    db = MagicMock()
    svc = LoanService(db)
    svc.get_active_loans_for_employee = MagicMock(return_value=[_loan()])
    svc._has_repayment_in_period = MagicMock(return_value=False)
    svc._has_linked_slip_deduction_in_period = MagicMock(return_value=True)

    result = svc.get_due_deductions(
        employee_id=uuid4(),
        period_start=date(2026, 2, 1),
        period_end=date(2026, 2, 28),
    )

    assert result == []


def test_get_due_deductions_respects_minimum_net_pay_ratio():
    db = MagicMock()
    svc = LoanService(db)
    svc.get_active_loans_for_employee = MagicMock(
        return_value=[
            _loan(monthly_installment="800.00", outstanding_balance="800.00"),
        ]
    )
    svc._has_repayment_in_period = MagicMock(return_value=False)
    svc._has_linked_slip_deduction_in_period = MagicMock(return_value=False)

    result = svc.get_due_deductions(
        employee_id=uuid4(),
        period_start=date(2026, 2, 1),
        period_end=date(2026, 2, 28),
        gross_pay=Decimal("1000.00"),
        total_existing_deductions=Decimal("500.00"),
    )

    assert len(result) == 1
    assert result[0].amount == Decimal("170.00")
