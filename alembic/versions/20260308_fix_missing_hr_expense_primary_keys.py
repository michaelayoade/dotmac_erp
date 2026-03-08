"""Repair missing primary keys on expense_claim and employee tables.

Revision ID: 20260308_fix_missing_hr_expense_pks
Revises: a1b2c3d4e5f6
Create Date: 2026-03-08
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "20260308_fix_missing_hr_expense_pks"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
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


def _primary_key_exists(schema: str, table: str) -> bool:
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
              AND con.contype = 'p'
            """
        ),
        {"schema": schema, "table": table},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    if not _primary_key_exists("expense", "expense_claim"):
        op.create_primary_key(
            "expense_claim_pkey",
            "expense_claim",
            ["claim_id"],
            schema="expense",
        )

    if not _primary_key_exists("hr", "employee"):
        op.create_primary_key(
            "employee_pkey",
            "employee",
            ["employee_id"],
            schema="hr",
        )


def downgrade() -> None:
    if _constraint_exists("hr", "employee", "employee_pkey"):
        op.drop_constraint(
            "employee_pkey",
            "employee",
            schema="hr",
            type_="primary",
        )
    if not _constraint_exists("hr", "employee", "uq_hr_employee_employee_id"):
        op.create_unique_constraint(
            "uq_hr_employee_employee_id",
            "employee",
            ["employee_id"],
            schema="hr",
        )

    if _constraint_exists("expense", "expense_claim", "expense_claim_pkey"):
        op.drop_constraint(
            "expense_claim_pkey",
            "expense_claim",
            schema="expense",
            type_="primary",
        )
    if not _constraint_exists("expense", "expense_claim", "uq_expense_claim_claim_id"):
        op.create_unique_constraint(
            "uq_expense_claim_claim_id",
            "expense_claim",
            ["claim_id"],
            schema="expense",
        )
