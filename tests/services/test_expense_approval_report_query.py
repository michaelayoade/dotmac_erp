from __future__ import annotations

from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.services.expense.expense_service import ExpenseService


class _EmptyResult:
    def all(self) -> list[object]:
        return []


class _CapturingSession:
    def __init__(self) -> None:
        self.statements: list[object] = []

    def execute(self, statement: object) -> _EmptyResult:
        self.statements.append(statement)
        return _EmptyResult()


def test_my_approvals_report_anchors_queries_on_approval_steps() -> None:
    db = _CapturingSession()
    organization_id = uuid4()

    ExpenseService(db).get_my_approvals_report(
        organization_id,
        approver_id=uuid4(),
        use_default_date_range=False,
    )

    assert len(db.statements) == 2
    for statement in db.statements:
        sql = str(
            statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        assert "FROM expense_claim_approval_step JOIN expense_claim" in sql
        assert "expense_claim_approval_step.organization_id" in sql
        assert "expense_claim.organization_id" in sql
