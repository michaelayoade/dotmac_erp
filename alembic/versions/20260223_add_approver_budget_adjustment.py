"""Add expense approver budget adjustment table.

One-time additive budget adjustments for a specific month, allowing
admins to increase (or decrease) an approver's monthly approval budget
for a single month without changing the base budget.

Revision ID: 20260223_add_approver_budget_adjustment
Revises: 20260221_add_audit_log_append_only_trigger
Create Date: 2026-02-23
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260223_add_approver_budget_adjustment"
down_revision: Union[str, None] = "20260221_add_audit_log_append_only_trigger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("expense_approver_budget_adjustment", schema="expense"):
        op.create_table(
            "expense_approver_budget_adjustment",
            sa.Column(
                "adjustment_id",
                postgresql.UUID(as_uuid=True),
                server_default=sa.text("gen_random_uuid()"),
                nullable=False,
            ),
            sa.Column(
                "approver_limit_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
            ),
            sa.Column(
                "organization_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
            ),
            sa.Column(
                "adjustment_month",
                sa.Date(),
                nullable=False,
                comment="First day of the target month, e.g. 2026-02-01",
            ),
            sa.Column(
                "additional_amount",
                sa.Numeric(precision=15, scale=2),
                nullable=False,
                comment="Additive adjustment to monthly budget (positive = increase)",
            ),
            sa.Column(
                "reason",
                sa.Text(),
                nullable=False,
                comment="Audit trail — why this adjustment was made",
            ),
            sa.Column(
                "adjusted_by_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("adjustment_id"),
            sa.ForeignKeyConstraint(
                ["approver_limit_id"],
                ["expense.expense_approver_limit.approver_limit_id"],
            ),
            sa.ForeignKeyConstraint(
                ["organization_id"],
                ["core_org.organization.organization_id"],
            ),
            sa.ForeignKeyConstraint(
                ["adjusted_by_id"],
                ["people.id"],
            ),
            sa.UniqueConstraint(
                "approver_limit_id",
                "adjustment_month",
                name="uq_approver_budget_adj_limit_month",
            ),
            sa.Index(
                "idx_approver_budget_adj_org",
                "organization_id",
            ),
            schema="expense",
        )


def downgrade() -> None:
    op.drop_table("expense_approver_budget_adjustment", schema="expense")
