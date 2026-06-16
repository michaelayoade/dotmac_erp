"""Add is_fixed_amount flag to tax_code.

Distinguishes a flat-levy tax (``tax_rate`` holds an absolute amount, e.g. a
per-transaction stamp duty) from a percentage tax (``tax_rate`` is a ratio).
Previously the rate type was only a UI affordance guessed from magnitude
(``tax_rate < 1``), while the calc engine always multiplied — so a "Fixed ₦50"
code computed base × 50. This column makes the distinction explicit.

Revision ID: 20260614_tax_code_is_fixed_amount
Revises: 20260613_merge_grn_push
Create Date: 2026-06-14
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260614_tax_code_is_fixed_amount"
down_revision = "20260613_merge_grn_push"
branch_labels = None
depends_on = None


def _has_column(schema: str, table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table, schema=schema):
        return False
    return any(
        col["name"] == column for col in inspector.get_columns(table, schema=schema)
    )


def upgrade() -> None:
    if not _has_column("tax", "tax_code", "is_fixed_amount"):
        op.add_column(
            "tax_code",
            sa.Column(
                "is_fixed_amount",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
                comment="When true, tax_rate is an absolute amount, not a ratio",
            ),
            schema="tax",
        )


def downgrade() -> None:
    if _has_column("tax", "tax_code", "is_fixed_amount"):
        op.drop_column("tax_code", "is_fixed_amount", schema="tax")
