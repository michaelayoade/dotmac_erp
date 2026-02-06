from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.models.people.payroll.payroll_entry import PayrollEntryStatus
from app.models.people.payroll.salary_slip import SalarySlipStatus
from app.services.people.payroll.payroll_gl_adapter import PayrollGLAdapter


def _make_component(
    code: str,
    expense_account_id: str | None = None,
    liability_account_id: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        component_id="component-id",
        component_code=code,
        component_name=code,
        expense_account_id=expense_account_id,
        liability_account_id=liability_account_id,
    )


def _make_earning(code: str, amount: str, expense_account_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        component=_make_component(code, expense_account_id=expense_account_id),
        component_name=code,
        amount=Decimal(amount),
        statistical_component=False,
        do_not_include_in_total=False,
    )


def _make_deduction(
    code: str,
    amount: str,
    liability_account_id: str,
    expense_account_id: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        component=_make_component(
            code,
            expense_account_id=expense_account_id,
            liability_account_id=liability_account_id,
        ),
        component_name=code,
        amount=Decimal(amount),
        statistical_component=False,
        do_not_include_in_total=(code == "PENSION_EMPLOYER"),
    )


def test_create_slip_journal_includes_employer_pension_expense():
    db = MagicMock()
    org_id = "00000000-0000-0000-0000-000000000001"
    user_id = "00000000-0000-0000-0000-000000000002"
    payroll_payable = "00000000-0000-0000-0000-000000000010"
    exp_salary = "00000000-0000-0000-0000-000000000011"
    exp_employer_pension = "00000000-0000-0000-0000-000000000012"
    liab_pension = "00000000-0000-0000-0000-000000000013"

    employee = SimpleNamespace(
        employee_id="emp",
        default_payroll_payable_account_id=None,
        cost_center_id=None,
    )
    organization = SimpleNamespace(salary_payable_account_id=payroll_payable)

    slip = SimpleNamespace(
        organization_id=org_id,
        slip_id="slip",
        slip_number="SLIP-2026-00001",
        status=SalarySlipStatus.APPROVED,
        employee_id=employee.employee_id,
        employee_name="Jane Doe",
        posting_date=date(2026, 1, 31),
        currency_code="NGN",
        exchange_rate=Decimal("1.0"),
        net_pay=Decimal("900"),
        gross_pay=Decimal("1000"),
        cost_center_id=None,
        journal_entry_id=None,
        earnings=[_make_earning("BASIC", "1000", exp_salary)],
        deductions=[
            _make_deduction(
                "PENSION_EMPLOYER", "100", liab_pension, exp_employer_pension
            )
        ],
    )

    def _get(model, obj_id):
        if model.__name__ == "Employee":
            return employee
        if model.__name__ == "Organization":
            return organization
        return None

    db.get.side_effect = _get

    with (
        patch(
            "app.services.people.payroll.payroll_gl_adapter.JournalService.create_journal"
        ) as create_journal,
        patch(
            "app.services.people.payroll.payroll_gl_adapter.JournalService.submit_journal"
        ),
        patch(
            "app.services.people.payroll.payroll_gl_adapter.JournalService.approve_journal"
        ),
        patch(
            "app.services.people.payroll.payroll_gl_adapter.LedgerPostingService.post_journal_entry"
        ) as post_entry,
    ):
        post_entry.return_value = SimpleNamespace(
            success=True, posting_batch_id="batch"
        )
        create_journal.return_value = SimpleNamespace(journal_entry_id="journal")

        result = PayrollGLAdapter.create_slip_journal(
            db=db,
            organization_id=org_id,
            slip=slip,
            posting_date=slip.posting_date,
            posted_by_user_id=user_id,
        )

    assert result.success is True
    journal_input = create_journal.call_args[0][2]
    lines = journal_input.lines
    assert any(
        line.account_id == exp_employer_pension and line.debit_amount == Decimal("100")
        for line in lines
    )


def test_create_run_journal_includes_employer_pension_expense():
    db = MagicMock()
    org_id = "00000000-0000-0000-0000-000000000001"
    user_id = "00000000-0000-0000-0000-000000000002"
    exp_employer_pension = "00000000-0000-0000-0000-000000000012"
    liab_pension = "00000000-0000-0000-0000-000000000013"

    organization = SimpleNamespace(
        salaries_expense_account_id="exp-salary",
        salary_payable_account_id="payable",
    )
    entry = SimpleNamespace(
        entry_id="entry",
        payroll_month=1,
        payroll_year=2026,
        start_date=date(2026, 1, 1),
        posting_date=date(2026, 1, 31),
        journal_entry_id=None,
        status=PayrollEntryStatus.APPROVED,
        entry_number="PAY-2026-0001",
    )
    slips = [
        SimpleNamespace(
            gross_pay=Decimal("1000"),
            net_pay=Decimal("900"),
            currency_code="NGN",
            exchange_rate=Decimal("1.0"),
            deductions=[
                _make_deduction(
                    "PENSION_EMPLOYER", "100", liab_pension, exp_employer_pension
                ),
            ],
        )
    ]

    def _get(model, obj_id):
        if model.__name__ == "Organization":
            return organization
        return None

    db.get.side_effect = _get

    with (
        patch(
            "app.services.people.payroll.payroll_gl_adapter.JournalService.create_journal"
        ) as create_journal,
        patch(
            "app.services.people.payroll.payroll_gl_adapter.JournalService.submit_journal"
        ),
        patch(
            "app.services.people.payroll.payroll_gl_adapter.JournalService.approve_journal"
        ),
        patch(
            "app.services.people.payroll.payroll_gl_adapter.LedgerPostingService.post_journal_entry"
        ) as post_entry,
    ):
        post_entry.return_value = SimpleNamespace(
            success=True, posting_batch_id="batch"
        )
        create_journal.return_value = SimpleNamespace(journal_entry_id="journal")

        result = PayrollGLAdapter.create_run_journal(
            db=db,
            organization_id=org_id,
            entry=entry,
            slips=slips,
            posting_date=date(2026, 1, 31),
            posted_by_user_id=user_id,
        )

    assert result.success is True
    journal_input = create_journal.call_args[0][2]
    lines = journal_input.lines
    assert any(
        line.account_id == exp_employer_pension and line.debit_amount == Decimal("100")
        for line in lines
    )
