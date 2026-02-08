"""Add recipient bank name to expense claims.

Revision ID: 20260207_add_expense_claim_bank_name
Revises: 20260206_merge_heads_cancel_reason
Create Date: 2026-02-07
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260207_add_expense_claim_bank_name"
down_revision = "20260206_merge_heads_cancel_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        col["name"] for col in inspector.get_columns("expense_claim", schema="expense")
    }
    if "recipient_bank_name" not in columns:
        op.add_column(
            "expense_claim",
            sa.Column("recipient_bank_name", sa.String(100), nullable=True),
            schema="expense",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        col["name"] for col in inspector.get_columns("expense_claim", schema="expense")
    }
    if "recipient_bank_name" in columns:
        op.drop_column("expense_claim", "recipient_bank_name", schema="expense")
