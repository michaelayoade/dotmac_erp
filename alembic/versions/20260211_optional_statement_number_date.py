"""Make bank statement number and date optional.

Revision ID: 20260211_optional_statement_number_date
Revises: 20260210_optional_statement_balances
Create Date: 2026-02-11
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260211_optional_statement_number_date"
down_revision = "20260210_optional_statement_balances"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {
        c["name"]: c for c in inspector.get_columns("bank_statements", schema="banking")
    }

    if "statement_number" in columns and not columns["statement_number"].get(
        "nullable"
    ):
        op.alter_column(
            "bank_statements",
            "statement_number",
            existing_type=sa.String(50),
            nullable=True,
            schema="banking",
        )

    if "statement_date" in columns and not columns["statement_date"].get("nullable"):
        op.alter_column(
            "bank_statements",
            "statement_date",
            existing_type=sa.Date(),
            nullable=True,
            schema="banking",
        )


def downgrade() -> None:
    # Backfill NULLs before restoring NOT NULL
    op.execute(
        sa.text("""
            UPDATE banking.bank_statements
            SET statement_number = 'STMT-' || LEFT(statement_id::text, 8)
            WHERE statement_number IS NULL
        """)
    )
    op.execute(
        sa.text("""
            UPDATE banking.bank_statements
            SET statement_date = period_start
            WHERE statement_date IS NULL
        """)
    )

    op.alter_column(
        "bank_statements",
        "statement_number",
        existing_type=sa.String(50),
        nullable=False,
        schema="banking",
    )
    op.alter_column(
        "bank_statements",
        "statement_date",
        existing_type=sa.Date(),
        nullable=False,
        schema="banking",
    )
