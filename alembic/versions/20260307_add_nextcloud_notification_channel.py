"""Add Nextcloud Talk notification channel.

Adds:
- NEXTCLOUD and ALL values to notificationchannel enum
- notification.nextcloud_sent and notification.nextcloud_sent_at columns
- people.nextcloud_user_id column for Nextcloud user mapping
- 'notifications' value to settingdomain enum

Revision ID: 20260307_nextcloud_talk
Revises: 20260304_add_vat_exempt_supplier_tax, 20260307_pll_idx
Create Date: 2026-03-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260307_nextcloud_talk"
down_revision = ("20260304_add_vat_exempt_supplier_tax", "20260307_pll_idx")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Extend the notificationchannel enum with new values
    op.execute("ALTER TYPE notificationchannel ADD VALUE IF NOT EXISTS 'NEXTCLOUD'")
    op.execute("ALTER TYPE notificationchannel ADD VALUE IF NOT EXISTS 'ALL'")

    # Add Nextcloud delivery tracking columns to notification table
    op.add_column(
        "notification",
        sa.Column(
            "nextcloud_sent",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        schema="public",
    )
    op.add_column(
        "notification",
        sa.Column("nextcloud_sent_at", sa.DateTime(), nullable=True),
        schema="public",
    )

    # Extend settingdomain enum for notifications settings
    op.execute("ALTER TYPE settingdomain ADD VALUE IF NOT EXISTS 'notifications'")

    # Add nextcloud_user_id to people table for user mapping
    op.add_column(
        "people",
        sa.Column("nextcloud_user_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("people", "nextcloud_user_id")
    op.drop_column("notification", "nextcloud_sent_at", schema="public")
    op.drop_column("notification", "nextcloud_sent", schema="public")
    # Note: PostgreSQL does not support removing enum values
