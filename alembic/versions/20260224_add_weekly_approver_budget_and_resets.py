"""Add weekly approver budget and manual reset audit table.

Revision ID: 20260224_add_weekly_approver_budget_and_resets
Revises: 20260223_add_approver_budget_adjustment
Create Date: 2026-02-24
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260224_add_weekly_approver_budget_and_resets"
down_revision: Union[str, None] = "20260223_add_approver_budget_adjustment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    columns = {
        c["name"]
        for c in inspector.get_columns("expense_approver_limit", schema="expense")
    }
    if "weekly_approval_budget" not in columns:
        op.add_column(
            "expense_approver_limit",
            sa.Column(
                "weekly_approval_budget",
                sa.Numeric(15, 2),
                nullable=True,
                comment="Weekly budget cap for total approvals. NULL = unlimited.",
            ),
            schema="expense",
        )

    if not inspector.has_table("expense_approver_limit_reset", schema="expense"):
        op.create_table(
            "expense_approver_limit_reset",
            sa.Column(
                "reset_id",
                postgresql.UUID(as_uuid=True),
                server_default=sa.text("gen_random_uuid()"),
                nullable=False,
            ),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("approver_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "approver_limit_id", postgresql.UUID(as_uuid=True), nullable=False
            ),
            sa.Column("reset_reason", sa.Text(), nullable=False),
            sa.Column("reviewed_by_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("reviewed_from", sa.Date(), nullable=True),
            sa.Column("reviewed_to", sa.Date(), nullable=True),
            sa.Column(
                "reviewed_claim_count", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column(
                "reset_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.PrimaryKeyConstraint("reset_id"),
            sa.ForeignKeyConstraint(
                ["organization_id"], ["core_org.organization.organization_id"]
            ),
            sa.ForeignKeyConstraint(["approver_id"], ["hr.employee.employee_id"]),
            sa.ForeignKeyConstraint(
                ["approver_limit_id"],
                ["expense.expense_approver_limit.approver_limit_id"],
            ),
            sa.ForeignKeyConstraint(["reviewed_by_id"], ["people.id"]),
            schema="expense",
        )
        op.create_index(
            "idx_approver_limit_reset_lookup",
            "expense_approver_limit_reset",
            [
                "organization_id",
                "approver_id",
                "approver_limit_id",
                "reset_at",
            ],
            schema="expense",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("expense_approver_limit_reset", schema="expense"):
        op.drop_index(
            "idx_approver_limit_reset_lookup",
            table_name="expense_approver_limit_reset",
            schema="expense",
        )
        op.drop_table("expense_approver_limit_reset", schema="expense")

    columns = {
        c["name"]
        for c in inspector.get_columns("expense_approver_limit", schema="expense")
    }
    if "weekly_approval_budget" in columns:
        op.drop_column(
            "expense_approver_limit", "weekly_approval_budget", schema="expense"
        )
