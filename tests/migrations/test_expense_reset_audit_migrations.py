"""Compile the additive migrations against PostgreSQL, without production access."""

import importlib.util
from io import StringIO
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations


def migration_sql(name, monkeypatch):
    path = Path(__file__).parents[2] / "alembic/versions" / name
    spec = importlib.util.spec_from_file_location("reset_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    monkeypatch.setattr(module, "op", Operations(context))
    module.upgrade()
    return output.getvalue()


def test_audit_upgrade_adds_nullable_history_without_fabricated_balances(monkeypatch):
    sql = migration_sql("20261104_expense_reset_audit.py", monkeypatch)
    assert "ADD COLUMN paid_at TIMESTAMP WITH TIME ZONE" in sql
    assert "ADD COLUMN remaining_before_reset NUMERIC(19, 4)" in sql
    assert "ADD COLUMN reviewer_name_snapshot" in sql
    assert "UPDATE expense.expense_claim" not in sql
    assert "SET remaining_before_reset" not in sql


def test_permission_upgrade_preserves_dedicated_reviewers_without_granting_approvers(
    monkeypatch,
):
    sql = migration_sql("20261104_expense_reset_permission.py", monkeypatch)
    assert "expense:limits:reset" in sql
    assert "('admin', 'expense_reviewer')" in sql
    assert "expense_approver" not in sql
    assert "ON CONFLICT" in sql
