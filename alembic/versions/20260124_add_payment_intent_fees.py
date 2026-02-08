"""Add fee tracking fields to payment_intent.

Revision ID: 20260124_payment_intent_fees
Revises: 20260124_expense_reimb_journal
Create Date: 2026-01-24
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "20260124_payment_intent_fees"
down_revision = "20260124_expense_reimb_journal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payment_intent",
        sa.Column(
            "fee_amount",
            sa.Numeric(19, 4),
            nullable=True,
            comment="Gateway fee charged (in currency units, not kobo)",
        ),
        schema="payments",
    )

    op.add_column(
        "payment_intent",
        sa.Column(
            "fee_journal_id",
            UUID(as_uuid=True),
            nullable=True,
            comment="GL journal entry for fee posting",
        ),
        schema="payments",
    )


def downgrade() -> None:
    op.drop_column("payment_intent", "fee_journal_id", schema="payments")
    op.drop_column("payment_intent", "fee_amount", schema="payments")
