"""Persist dynamic calendar department and designation recipient targets."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260921_calendar_recipient_targets"
down_revision = "20260920_people_calendar_optional_nextcloud"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organization_calendar_events",
        sa.Column(
            "recipient_targets",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("organization_calendar_events", "recipient_targets", schema="public")
