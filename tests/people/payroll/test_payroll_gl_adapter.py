from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.models.people.payroll.payroll_entry import PayrollEntryStatus
from app.models.people.payroll.salary_slip import SalarySlipStatus
from app.services.people.payroll.payroll_gl_adapter import PayrollGLAdapter


def _slip(cost_center_id=None, employee_id=None) -> SimpleNamespace:
    """Minimal slip-like object for _shared_cost_center_id testing."""
    return SimpleNamespace(
        cost_center_id=cost_center_id,
        employee_id=employee_id,
    )


def _mock_db_employees(emp_cc_pairs: list[tuple]) -> MagicMock:
    """Build a MagicMock Session whose execute().all() returns emp_cc_pairs."""
    db = MagicMock()
    db.execute.return_value.all.return_value = emp_cc_pairs
    return db


# ── _shared_cost_center_id ──────────────────────────────────────────────────


def test_shared_cc_empty_list_returns_none():
    assert PayrollGLAdapter._shared_cost_center_id(MagicMock(), []) is None


def test_shared_cc_all_slips_have_same_slip_level_cc():
    cc = uuid4()
    slips = [_slip(cost_center_id=cc) for _ in range(3)]
    db = _mock_db_employees([])  # no employee lookup needed
    assert PayrollGLAdapter._shared_cost_center_id(db, slips) == cc
    db.execute.assert_not_called()  # no employee query when slips carry CC


def test_shared_cc_falls_back_to_employee_when_slip_cc_none():
    cc = uuid4()
    emp_a, emp_b = uuid4(), uuid4()
    slips = [_slip(employee_id=emp_a), _slip(employee_id=emp_b)]
    db = _mock_db_employees([(emp_a, cc), (emp_b, cc)])
    assert PayrollGLAdapter._shared_cost_center_id(db, slips) == cc
    db.execute.assert_called_once()  # batched, not per-slip


def test_shared_cc_slip_level_overrides_employee_level():
    slip_cc = uuid4()
    emp_cc = uuid4()
    emp_id = uuid4()
    slips = [_slip(cost_center_id=slip_cc, employee_id=emp_id)]
    db = _mock_db_employees([(emp_id, emp_cc)])  # would never be called
    assert PayrollGLAdapter._shared_cost_center_id(db, slips) == slip_cc


def test_shared_cc_disagreement_returns_none():
    cc_a, cc_b = uuid4(), uuid4()
    slips = [_slip(cost_center_id=cc_a), _slip(cost_center_id=cc_b)]
    db = _mock_db_employees([])
    assert PayrollGLAdapter._shared_cost_center_id(db, slips) is None


def test_shared_cc_any_unresolved_slip_returns_none():
    cc = uuid4()
    emp_id = uuid4()
    slips = [
        _slip(cost_center_id=cc),
        _slip(employee_id=emp_id),  # employee has no CC in DB
    ]
    db = _mock_db_employees([(emp_id, None)])
    assert PayrollGLAdapter._shared_cost_center_id(db, slips) is None


def test_shared_cc_employee_not_found_returns_none():
    emp_id = uuid4()
    slips = [_slip(employee_id=emp_id)]
    db = _mock_db_employees([])  # employee row missing entirely
    assert PayrollGLAdapter._shared_cost_center_id(db, slips) is None


def test_shared_cc_mixed_slip_and_employee_sources_agreeing():
    cc = uuid4()
    emp_id = uuid4()
    slips = [
        _slip(cost_center_id=cc),  # via slip
        _slip(employee_id=emp_id),  # via employee fallback
    ]
    db = _mock_db_employees([(emp_id, cc)])
    assert PayrollGLAdapter._shared_cost_center_id(db, slips) == cc


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
            "app.services.people.payroll.payroll_gl_adapter.BasePostingAdapter.create_and_approve_journal"
        ) as create_and_approve,
        patch(
            "app.services.people.payroll.payroll_gl_adapter.BasePostingAdapter.post_to_ledger"
        ) as post_entry,
    ):
        post_entry.return_value = SimpleNamespace(
            success=True, posting_batch_id="batch", message="Posted successfully"
        )
        create_and_approve.return_value = (
            SimpleNamespace(journal_entry_id="journal"),
            None,
        )

        result = PayrollGLAdapter.create_slip_journal(
            db=db,
            organization_id=org_id,
            slip=slip,
            posting_date=slip.posting_date,
            posted_by_user_id=user_id,
        )

    assert result.success is True
    journal_input = create_and_approve.call_args[0][2]
    lines = journal_input.lines
    assert any(
        line.account_id == exp_employer_pension and line.debit_amount == Decimal("100")
        for line in lines
    )


def test_create_run_journal_deduction_without_liability_falls_back_to_payable():
    """F34: a non-statutory deduction with no liability account must fall back to
    the salary-payable account so it is still credited and the run journal stays
    balanced (it was already subtracted from net_pay)."""
    db = MagicMock()
    org_id = "00000000-0000-0000-0000-000000000001"
    user_id = "00000000-0000-0000-0000-000000000002"
    payable = "00000000-0000-0000-0000-000000000099"

    organization = SimpleNamespace(
        salaries_expense_account_id="exp-salary",
        salary_payable_account_id=payable,
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
    # Gross 1000, a 100 deduction with NO liability account, net 900.
    slips = [
        SimpleNamespace(
            gross_pay=Decimal("1000"),
            net_pay=Decimal("900"),
            currency_code="NGN",
            exchange_rate=Decimal("1.0"),
            deductions=[
                _make_deduction("UNION_DUES", "100", None),
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
            "app.services.people.payroll.payroll_gl_adapter.BasePostingAdapter.create_and_approve_journal"
        ) as create_and_approve,
        patch(
            "app.services.people.payroll.payroll_gl_adapter.BasePostingAdapter.post_to_ledger"
        ) as post_entry,
    ):
        post_entry.return_value = SimpleNamespace(
            success=True, posting_batch_id="batch", message="Posted successfully"
        )
        create_and_approve.return_value = (
            SimpleNamespace(journal_entry_id="journal"),
            None,
        )

        result = PayrollGLAdapter.create_run_journal(
            db=db,
            organization_id=org_id,
            entry=entry,
            slips=slips,
            posting_date=date(2026, 1, 31),
            posted_by_user_id=user_id,
        )

    assert result.success is True
    journal_input = create_and_approve.call_args[0][2]
    lines = journal_input.lines

    # Journal must balance: total debits == total credits.
    total_debit = sum((line.debit_amount for line in lines), Decimal("0"))
    total_credit = sum((line.credit_amount for line in lines), Decimal("0"))
    assert total_debit == total_credit == Decimal("1000")

    # The 100 deduction is credited to the payable fallback (in addition to the
    # 900 net-pay credit, both to `payable`).
    payable_credits = sum(
        (line.credit_amount for line in lines if line.account_id == payable),
        Decimal("0"),
    )
    assert payable_credits == Decimal("1000")  # 900 net + 100 fallback deduction


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
            "app.services.people.payroll.payroll_gl_adapter.BasePostingAdapter.create_and_approve_journal"
        ) as create_and_approve,
        patch(
            "app.services.people.payroll.payroll_gl_adapter.BasePostingAdapter.post_to_ledger"
        ) as post_entry,
    ):
        post_entry.return_value = SimpleNamespace(
            success=True, posting_batch_id="batch", message="Posted successfully"
        )
        create_and_approve.return_value = (
            SimpleNamespace(journal_entry_id="journal"),
            None,
        )

        result = PayrollGLAdapter.create_run_journal(
            db=db,
            organization_id=org_id,
            entry=entry,
            slips=slips,
            posting_date=date(2026, 1, 31),
            posted_by_user_id=user_id,
        )

    assert result.success is True
    journal_input = create_and_approve.call_args[0][2]
    lines = journal_input.lines
    assert any(
        line.account_id == exp_employer_pension and line.debit_amount == Decimal("100")
        for line in lines
    )
