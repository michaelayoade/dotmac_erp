"""Add expense claim approval steps.

Revision ID: 20260308_expense_approval_steps
Revises: e0696f5adbeb
Create Date: 2026-03-08
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260308_expense_approval_steps"
down_revision: Union[str, None] = "e0696f5adbeb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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
