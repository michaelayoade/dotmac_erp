"""Add configurable probation restriction to leave types."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "20260909_leave_probation_restriction"
down_revision: str | Sequence[str] | None = (
    "20260815_academy_course_projection",
    "20260815_add_academy_learning_sync",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "leave_type",
        sa.Column(
            "restricted_during_probation",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="Employees must complete probation before requesting this leave",
        ),
        schema="leave",
    )
    op.execute(
        sa.text(
            "UPDATE leave.leave_type "
            "SET restricted_during_probation = TRUE "
            "WHERE upper(coalesce(leave_type_code, '')) LIKE '%ANNUAL%' "
            "OR upper(coalesce(leave_type_name, '')) LIKE '%ANNUAL%'"
        )
    )


def downgrade() -> None:
    op.drop_column("leave_type", "restricted_during_probation", schema="leave")
