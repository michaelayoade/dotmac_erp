"""Allow ERP calendar participants without a Nextcloud identity."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260920_people_calendar_optional_nextcloud"
down_revision = "20260920_departmental_kpis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "organization_calendar_participants",
        "nextcloud_user_id",
        existing_type=sa.String(length=255),
        nullable=True,
        schema="public",
    )


def downgrade() -> None:
    op.alter_column(
        "organization_calendar_participants",
        "nextcloud_user_id",
        existing_type=sa.String(length=255),
        nullable=False,
        schema="public",
    )
