"""Add recipient name to expense claims.

Revision ID: 20260207_add_expense_claim_recipient_name
Revises: 20260207_add_expense_claim_bank_name
Create Date: 2026-02-07
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260207_add_expense_claim_recipient_name"
down_revision = "20260207_add_expense_claim_bank_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        col["name"] for col in inspector.get_columns("expense_claim", schema="expense")
    }
    if "recipient_name" not in columns:
        op.add_column(
            "expense_claim",
            sa.Column("recipient_name", sa.String(150), nullable=True),
            schema="expense",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        col["name"] for col in inspector.get_columns("expense_claim", schema="expense")
    }
    if "recipient_name" in columns:
        op.drop_column("expense_claim", "recipient_name", schema="expense")
