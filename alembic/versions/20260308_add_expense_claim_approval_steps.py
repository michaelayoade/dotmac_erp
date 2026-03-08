"""Add expense claim approval steps.

Revision ID: 20260308_expense_approval_steps
Revises: dcbb2ab19c94
Create Date: 2026-03-08
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260308_expense_approval_steps"
down_revision: Union[str, None] = "dcbb2ab19c94"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _single_column_unique_exists(schema: str, table: str, column: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace
            JOIN unnest(con.conkey) WITH ORDINALITY AS cols(attnum, ord) ON TRUE
            JOIN pg_attribute attr
              ON attr.attrelid = rel.oid
             AND attr.attnum = cols.attnum
            WHERE nsp.nspname = :schema
              AND rel.relname = :table
              AND con.contype IN ('p', 'u')
            GROUP BY con.oid
            HAVING string_agg(attr.attname::text, ',' ORDER BY cols.ord) = :column
            """
        ),
        {"schema": schema, "table": table, "column": column},
    )
    return result.fetchone() is not None


def _named_constraint_exists(schema: str, table: str, constraint_name: str) -> bool:
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
        {
            "schema": schema,
            "table": table,
            "constraint_name": constraint_name,
        },
    )
    return result.fetchone() is not None


def upgrade() -> None:
    if not _single_column_unique_exists("expense", "expense_claim", "claim_id"):
        op.create_unique_constraint(
            "uq_expense_claim_claim_id",
            "expense_claim",
            ["claim_id"],
            schema="expense",
        )
    if not _single_column_unique_exists("hr", "employee", "employee_id"):
        op.create_unique_constraint(
            "uq_hr_employee_employee_id",
            "employee",
            ["employee_id"],
            schema="hr",
        )

    op.create_table(
        "expense_claim_approval_step",
        sa.Column(
            "approval_step_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "submission_round",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("approver_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approver_name", sa.String(length=200), nullable=False),
        sa.Column("max_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "requires_all_approvals",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "is_escalation",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("decision", sa.String(length=20), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["expense.expense_claim.claim_id"],
        ),
        sa.ForeignKeyConstraint(
            ["approver_id"],
            ["hr.employee.employee_id"],
        ),
        sa.PrimaryKeyConstraint("approval_step_id"),
        sa.UniqueConstraint(
            "claim_id",
            "submission_round",
            "step_number",
            name="uq_expense_claim_approval_step_round",
        ),
        schema="expense",
    )
    op.create_index(
        "idx_expense_claim_approval_step_claim_round",
        "expense_claim_approval_step",
        ["claim_id", "submission_round"],
        schema="expense",
    )
    op.create_index(
        "idx_expense_claim_approval_step_pending",
        "expense_claim_approval_step",
        ["organization_id", "approver_id", "decision"],
        schema="expense",
    )
    op.create_index(
        "ix_expense_expense_claim_approval_step_organization_id",
        "expense_claim_approval_step",
        ["organization_id"],
        schema="expense",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_expense_expense_claim_approval_step_organization_id",
        table_name="expense_claim_approval_step",
        schema="expense",
    )
    op.drop_index(
        "idx_expense_claim_approval_step_pending",
        table_name="expense_claim_approval_step",
        schema="expense",
    )
    op.drop_index(
        "idx_expense_claim_approval_step_claim_round",
        table_name="expense_claim_approval_step",
        schema="expense",
    )
    op.drop_table("expense_claim_approval_step", schema="expense")
    if _named_constraint_exists("hr", "employee", "uq_hr_employee_employee_id"):
        op.drop_constraint(
            "uq_hr_employee_employee_id",
            "employee",
            schema="hr",
            type_="unique",
        )
    if _named_constraint_exists(
        "expense", "expense_claim", "uq_expense_claim_claim_id"
    ):
        op.drop_constraint(
            "uq_expense_claim_claim_id",
            "expense_claim",
            schema="expense",
            type_="unique",
        )
