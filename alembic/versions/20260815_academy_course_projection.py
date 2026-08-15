"""Map ERP courses to stable Academy course references.

Revision ID: 20260815_academy_course_projection
Revises: 20260814_database_roles, 20260814_sub_operational_sync_v2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_academy_course_projection"
down_revision: str | tuple[str, ...] = (
    "20260814_database_roles",
    "20260814_sub_operational_sync_v2",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "training_course",
        sa.Column("academy_course_ref", sa.String(length=120), nullable=True),
        schema="training",
    )
    op.create_index(
        "uq_training_course_academy_ref",
        "training_course",
        ["organization_id", "academy_course_ref"],
        unique=True,
        schema="training",
        postgresql_where=sa.text("academy_course_ref IS NOT NULL"),
    )
    op.add_column(
        "training_course_progress",
        sa.Column("academy_updated_at", sa.DateTime(timezone=True), nullable=True),
        schema="training",
    )


def downgrade() -> None:
    op.drop_column("training_course_progress", "academy_updated_at", schema="training")
    op.drop_index(
        "uq_training_course_academy_ref",
        table_name="training_course",
        schema="training",
    )
    op.drop_column("training_course", "academy_course_ref", schema="training")
