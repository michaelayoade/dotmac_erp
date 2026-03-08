"""Rebind approval-step foreign keys to primary keys and drop helper uniques.

Revision ID: 20260308_rebind_expense_hr_fks
Revises: 20260308_fix_missing_hr_expense_pks
Create Date: 2026-03-08
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "20260308_rebind_expense_hr_fks"
down_revision: Union[str, None] = "20260308_fix_missing_hr_expense_pks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _constraint_exists(schema: str, table: str, constraint_name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
            WHERE nsp.nspname = :schema
              AND rel.relname = :table
              AND con.conname = :constraint_name
            """
        ),
        {"schema": schema, "table": table, "constraint_name": constraint_name},
    )
    return result.fetchone() is not None


def _table_exists(schema: str, table: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = :schema
              AND table_name = :table
            """
        ),
        {"schema": schema, "table": table},
    )
    return result.fetchone() is not None


def _drop_approval_step_fks() -> None:
    if not _table_exists("expense", "expense_claim_approval_step"):
        return
    for name in (
        "expense_claim_approval_step_claim_id_fkey",
        "expense_claim_approval_step_approver_id_fkey",
    ):
        if _constraint_exists("expense", "expense_claim_approval_step", name):
            op.drop_constraint(
                name,
                "expense_claim_approval_step",
                schema="expense",
                type_="foreignkey",
            )


def _create_approval_step_fks() -> None:
    if not _table_exists("expense", "expense_claim_approval_step"):
        return
    if not _constraint_exists(
        "expense",
        "expense_claim_approval_step",
        "expense_claim_approval_step_claim_id_fkey",
    ):
        op.create_foreign_key(
            "expense_claim_approval_step_claim_id_fkey",
            "expense_claim_approval_step",
            "expense_claim",
            ["claim_id"],
            ["claim_id"],
            source_schema="expense",
            referent_schema="expense",
        )
    if not _constraint_exists(
        "expense",
        "expense_claim_approval_step",
        "expense_claim_approval_step_approver_id_fkey",
    ):
        op.create_foreign_key(
            "expense_claim_approval_step_approver_id_fkey",
            "expense_claim_approval_step",
            "employee",
            ["approver_id"],
            ["employee_id"],
            source_schema="expense",
            referent_schema="hr",
        )


def upgrade() -> None:
    _drop_approval_step_fks()

    if _constraint_exists("expense", "expense_claim", "uq_expense_claim_claim_id"):
        op.drop_constraint(
            "uq_expense_claim_claim_id",
            "expense_claim",
            schema="expense",
            type_="unique",
        )
    if _constraint_exists("hr", "employee", "uq_hr_employee_employee_id"):
        op.drop_constraint(
            "uq_hr_employee_employee_id",
            "employee",
            schema="hr",
            type_="unique",
        )

    _create_approval_step_fks()


def downgrade() -> None:
    _drop_approval_step_fks()

    if not _constraint_exists("expense", "expense_claim", "uq_expense_claim_claim_id"):
        op.create_unique_constraint(
            "uq_expense_claim_claim_id",
            "expense_claim",
            ["claim_id"],
            schema="expense",
        )
    if not _constraint_exists("hr", "employee", "uq_hr_employee_employee_id"):
        op.create_unique_constraint(
            "uq_hr_employee_employee_id",
            "employee",
            ["employee_id"],
            schema="hr",
        )

    _create_approval_step_fks()
