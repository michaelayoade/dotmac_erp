"""Make bank statement opening/closing balances optional.

Revision ID: 20260210_optional_statement_balances
Revises: 20260210_add_vehicle_license_location
Create Date: 2026-02-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260210_optional_statement_balances"
down_revision = "20260210_add_vehicle_license_location"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Make opening_balance nullable
    op.alter_column(
        "bank_statements",
        "opening_balance",
        existing_type=sa.Numeric(19, 4),
        nullable=True,
        schema="banking",
    )
    # Make closing_balance nullable
    op.alter_column(
        "bank_statements",
        "closing_balance",
        existing_type=sa.Numeric(19, 4),
        nullable=True,
        schema="banking",
    )


def downgrade() -> None:
    # Backfill NULLs with 0 before restoring NOT NULL
    op.execute(
        "UPDATE banking.bank_statements SET opening_balance = 0 WHERE opening_balance IS NULL"
    )
    op.execute(
        "UPDATE banking.bank_statements SET closing_balance = 0 WHERE closing_balance IS NULL"
    )
    op.alter_column(
        "bank_statements",
        "opening_balance",
        existing_type=sa.Numeric(19, 4),
        nullable=False,
        schema="banking",
    )
    op.alter_column(
        "bank_statements",
        "closing_balance",
        existing_type=sa.Numeric(19, 4),
        nullable=False,
        schema="banking",
    )
