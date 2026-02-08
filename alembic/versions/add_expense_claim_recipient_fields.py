"""add expense claim recipient fields

Revision ID: 9b2a7c1d4c9a
Revises: a0ea784077e8
Create Date: 2026-01-24
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "9b2a7c1d4c9a"
down_revision = "a0ea784077e8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "expense_claim",
        sa.Column("recipient_bank_code", sa.String(length=20), nullable=True),
        schema="expense",
    )
    op.add_column(
        "expense_claim",
        sa.Column("recipient_account_number", sa.String(length=20), nullable=True),
        schema="expense",
    )


def downgrade():
    op.drop_column("expense_claim", "recipient_account_number", schema="expense")
    op.drop_column("expense_claim", "recipient_bank_code", schema="expense")
