from types import SimpleNamespace
from uuid import uuid4

from app.services.expense.expense_posting_adapter import ExpensePostingAdapter


def test_get_employee_payable_account_uses_salary_payable_setting() -> None:
    org_id = uuid4()
    account_id = uuid4()
    db = SimpleNamespace()
    db.get = lambda _model, _org_id: SimpleNamespace(
        salary_payable_account_id=account_id
    )
    db.scalar = lambda _stmt: None

    result = ExpensePostingAdapter._get_employee_payable_account(db, org_id)

    assert result == account_id


def test_get_employee_payable_account_falls_back_to_account_lookup() -> None:
    org_id = uuid4()
    fallback_account_id = uuid4()
    db = SimpleNamespace()
    db.get = lambda _model, _org_id: SimpleNamespace(salary_payable_account_id=None)
    db.scalar = lambda _stmt: SimpleNamespace(account_id=fallback_account_id)

    result = ExpensePostingAdapter._get_employee_payable_account(db, org_id)

    assert result == fallback_account_id
