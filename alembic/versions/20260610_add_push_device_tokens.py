"""Add mobile push delivery: device_token table + notification.push_sent.

Adds:
- public.device_token table (FCM registration tokens per person/device)
- notification.push_sent / notification.push_sent_at delivery-tracking
  columns (mirrors email_sent / nextcloud_sent)

Revision ID: 20260610_push_devices
Revises: 20260603_add_match_state
Create Date: 2026-06-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260610_push_devices"
down_revision = "20260603_add_match_state"
branch_labels = None
depends_on = None


def _has_table(bind, name: str, schema: str = "public") -> bool:
    return sa.inspect(bind).has_table(name, schema=schema)


def _has_column(bind, table: str, column: str, schema: str = "public") -> bool:
    columns = sa.inspect(bind).get_columns(table, schema=schema)
    return any(col["name"] == column for col in columns)


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "device_token"):
        op.create_table(
            "device_token",
            sa.Column(
                "device_token_id",
                sa.dialects.postgresql.UUID(as_uuid=True),
                primary_key=True,
            ),
            sa.Column(
                "organization_id",
                sa.dialects.postgresql.UUID(as_uuid=True),
                sa.ForeignKey("core_org.organization.organization_id"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "person_id",
                sa.dialects.postgresql.UUID(as_uuid=True),
                sa.ForeignKey("people.id"),
                nullable=False,
                index=True,
            ),
            sa.Column("token", sa.String(512), nullable=False, unique=True),
            sa.Column("platform", sa.String(16), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            schema="public",
        )
        op.create_index(
            "ix_device_token_person_active",
            "device_token",
            ["person_id", "revoked_at"],
            schema="public",
        )

    if not _has_column(bind, "notification", "push_sent"):
        op.add_column(
            "notification",
            sa.Column(
                "push_sent",
                sa.Boolean(),
                nullable=False,
                server_default="false",
            ),
            schema="public",
        )
    if not _has_column(bind, "notification", "push_sent_at"):
        op.add_column(
            "notification",
            sa.Column("push_sent_at", sa.DateTime(), nullable=True),
            schema="public",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "notification", "push_sent_at"):
        op.drop_column("notification", "push_sent_at", schema="public")
    if _has_column(bind, "notification", "push_sent"):
        op.drop_column("notification", "push_sent", schema="public")
    if _has_table(bind, "device_token"):
        op.drop_table("device_token", schema="public")
