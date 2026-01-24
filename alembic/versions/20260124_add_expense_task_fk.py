"""Add task FK to expense_claim.

Revision ID: 20260124_expense_task_fk
Revises: 20260124_sequence_types_ops
Create Date: 2026-01-24
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "20260124_expense_task_fk"
down_revision = "20260124_sequence_types_ops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "expense_claim",
        sa.Column(
            "task_id",
            UUID(as_uuid=True),
            nullable=True,
            comment="Related project task",
        ),
        schema="expense",
    )

    op.create_foreign_key(
        "fk_expense_claim_task",
        "expense_claim",
        "task",
        ["task_id"],
        ["task_id"],
        source_schema="expense",
        referent_schema="pm",
    )

    op.create_index(
        "idx_expense_claim_task",
        "expense_claim",
        ["task_id"],
        schema="expense",
    )


def downgrade() -> None:
    op.drop_index("idx_expense_claim_task", table_name="expense_claim", schema="expense")
    op.drop_constraint("fk_expense_claim_task", "expense_claim", type_="foreignkey", schema="expense")
    op.drop_column("expense_claim", "task_id", schema="expense")
