"""Add durable retry state for notification-email delivery.

Failed notification emails previously stayed eligible for every one-minute
dispatch batch. The retry state supports bounded backoff and a terminal
dead-letter state without discarding the notification record.
"""

from __future__ import annotations

from sqlalchemy import inspect

from alembic import op

revision = "20260821_add_notification_email_retry_state"
down_revision = "20260819_repair_stale_admin_email_routing"
branch_labels = None
depends_on = None

_COLUMNS: dict[str, str] = {
    "email_retry_count": "INTEGER NOT NULL DEFAULT 0",
    "email_next_retry_at": "TIMESTAMPTZ",
    "email_dead_lettered": "BOOLEAN NOT NULL DEFAULT FALSE",
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {
        column["name"]
        for column in inspector.get_columns("notification", schema="public")
    }
    for name, ddl_type in _COLUMNS.items():
        if name not in columns:
            op.execute(f"ALTER TABLE public.notification ADD COLUMN {name} {ddl_type}")

    indexes = {
        index["name"]
        for index in inspector.get_indexes("notification", schema="public")
    }
    if "idx_notification_email_dispatch" not in indexes:
        op.execute(
            "CREATE INDEX idx_notification_email_dispatch "
            "ON public.notification "
            "(email_sent, email_dead_lettered, email_next_retry_at, created_at)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    indexes = {
        index["name"]
        for index in inspector.get_indexes("notification", schema="public")
    }
    if "idx_notification_email_dispatch" in indexes:
        op.execute("DROP INDEX public.idx_notification_email_dispatch")

    columns = {
        column["name"]
        for column in inspector.get_columns("notification", schema="public")
    }
    for name in _COLUMNS:
        if name in columns:
            op.execute(f"ALTER TABLE public.notification DROP COLUMN {name}")
