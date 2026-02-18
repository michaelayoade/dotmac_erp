"""Add rotating day/night work day columns to scheduling.shift_pattern.

Revision ID: 20260218_add_rotating_pattern_work_days
Revises: 20260218_add_gl_balance_check
Create Date: 2026-02-18
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260218_add_rotating_pattern_work_days"
down_revision = "20260218_add_gl_balance_check"
branch_labels = None
depends_on = None


def _column_names(schema: str, table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {col["name"] for col in inspector.get_columns(table, schema=schema)}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("shift_pattern", schema="scheduling"):
        return

    columns = _column_names("scheduling", "shift_pattern")

    if "day_work_days" not in columns:
        op.add_column(
            "shift_pattern",
            sa.Column(
                "day_work_days", postgresql.JSONB(astext_type=sa.Text()), nullable=True
            ),
            schema="scheduling",
        )

    if "night_work_days" not in columns:
        op.add_column(
            "shift_pattern",
            sa.Column(
                "night_work_days",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
            schema="scheduling",
        )

    # Backfill existing rotating patterns to preserve behavior.
    op.execute(
        """
        UPDATE scheduling.shift_pattern
        SET day_work_days = work_days
        WHERE rotation_type = 'ROTATING' AND day_work_days IS NULL
        """
    )
    op.execute(
        """
        UPDATE scheduling.shift_pattern
        SET night_work_days = work_days
        WHERE rotation_type = 'ROTATING' AND night_work_days IS NULL
        """
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("shift_pattern", schema="scheduling"):
        return

    columns = _column_names("scheduling", "shift_pattern")

    if "night_work_days" in columns:
        op.drop_column("shift_pattern", "night_work_days", schema="scheduling")

    if "day_work_days" in columns:
        op.drop_column("shift_pattern", "day_work_days", schema="scheduling")
