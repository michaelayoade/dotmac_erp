"""Move ERP calendar delivery to Talk notifications and ERP-owned reminders.

Revision ID: 20260920_calendar_talk
Revises: 20260919_self_calendar
Create Date: 2026-09-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260920_calendar_talk"
down_revision = "20260919_self_calendar"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions rp
        USING permissions p
        WHERE rp.permission_id = p.id
          AND p.key = 'calendar:sync:retry'
        """
    )
    op.execute("DELETE FROM permissions WHERE key = 'calendar:sync:retry'")
    op.add_column(
        "organization_calendar_reminders",
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )
    op.add_column(
        "organization_calendar_reminders",
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )
    op.execute(
        """
        UPDATE public.organization_calendar_reminders AS reminder
        SET scheduled_for = CASE
            WHEN event.all_day THEN
                (event.start_date::timestamp AT TIME ZONE event.timezone)
                - make_interval(mins => reminder.offset_minutes)
            ELSE event.start_at - make_interval(mins => reminder.offset_minutes)
        END
        FROM public.organization_calendar_events AS event
        WHERE event.event_id = reminder.event_id
        """
    )
    op.alter_column(
        "organization_calendar_reminders",
        "scheduled_for",
        nullable=False,
        schema="public",
    )
    op.create_index(
        "idx_org_calendar_reminder_due",
        "organization_calendar_reminders",
        ["scheduled_for", "dispatched_at"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_org_calendar_reminder_due",
        table_name="organization_calendar_reminders",
        schema="public",
    )
    op.drop_column("organization_calendar_reminders", "dispatched_at", schema="public")
    op.drop_column("organization_calendar_reminders", "scheduled_for", schema="public")
    op.execute(
        """
        INSERT INTO permissions (id, key, description, is_active, created_at, updated_at)
        VALUES (
            gen_random_uuid(),
            'calendar:sync:retry',
            'Retry failed organization calendar synchronization',
            TRUE,
            NOW(),
            NOW()
        )
        ON CONFLICT (key) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO role_permissions (id, role_id, permission_id)
        SELECT gen_random_uuid(), r.id, p.id
        FROM roles r CROSS JOIN permissions p
        WHERE lower(r.name) IN (
            'admin', 'super_admin', 'system_admin',
            'finance_manager', 'finance_director'
        )
          AND p.key = 'calendar:sync:retry'
        ON CONFLICT (role_id, permission_id) DO NOTHING
        """
    )
