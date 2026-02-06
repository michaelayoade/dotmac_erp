"""Add recipient_account_name to expense_claim.

Revision ID: 20260201_recipient_name
Revises:
Create Date: 2026-02-01

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260201_recipient_name"
down_revision: Union[str, None] = "20260201_add_remita_rrr_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add recipient_account_name column."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Check if column already exists
    if inspector.has_table("expense_claim", schema="expense"):
        columns = {
            col["name"]
            for col in inspector.get_columns("expense_claim", schema="expense")
        }
        if "recipient_account_name" not in columns:
            op.add_column(
                "expense_claim",
                sa.Column(
                    "recipient_account_name",
                    sa.String(100),
                    nullable=True,
                    comment="Verified account holder name from Paystack",
                ),
                schema="expense",
            )


def downgrade() -> None:
    """Remove recipient_account_name column."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("expense_claim", schema="expense"):
        columns = {
            col["name"]
            for col in inspector.get_columns("expense_claim", schema="expense")
        }
        if "recipient_account_name" in columns:
            op.drop_column("expense_claim", "recipient_account_name", schema="expense")
