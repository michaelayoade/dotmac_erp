"""Add private self-service events to the organizational calendar foundation.

Revision ID: 20260919_self_calendar
Revises: 20260919_organization_calendar
Create Date: 2026-09-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260919_self_calendar"
down_revision = "20260919_organization_calendar"
branch_labels = None
depends_on = None

PERSONAL_PERMISSIONS = {
    "calendar:personal:access": "Access My Calendar in employee self service",
    "calendar:personal:create": "Create and manage personal calendar events",
    "calendar:personal:invite": "Invite eligible employees to personal events",
}


def upgrade() -> None:
    op.add_column(
        "organization_calendar_events",
        sa.Column(
            "event_scope",
            sa.String(20),
            nullable=False,
            server_default="ORGANIZATIONAL",
        ),
        schema="public",
    )
    op.create_check_constraint(
        "ck_org_calendar_event_scope",
        "organization_calendar_events",
        "event_scope IN ('ORGANIZATIONAL', 'PERSONAL')",
        schema="public",
    )
    op.create_index(
        "idx_org_calendar_event_org_scope_start",
        "organization_calendar_events",
        ["organization_id", "event_scope", "start_at", "start_date"],
        schema="public",
    )

    for key, description in PERSONAL_PERMISSIONS.items():
        op.execute(
            sa.text(
                """
                INSERT INTO permissions (id, key, description, is_active, created_at, updated_at)
                VALUES (gen_random_uuid(), :key, :description, TRUE, NOW(), NOW())
                ON CONFLICT (key) DO UPDATE
                SET description = EXCLUDED.description, is_active = TRUE, updated_at = NOW()
                """
            ).bindparams(key=key, description=description)
        )

    op.execute(
        """
        INSERT INTO role_permissions (id, role_id, permission_id)
        SELECT gen_random_uuid(), r.id, p.id
        FROM roles r CROSS JOIN permissions p
        WHERE lower(r.name) IN ('employee', 'admin', 'super_admin', 'system_admin')
          AND p.key IN (
            'calendar:personal:access',
            'calendar:personal:create',
            'calendar:personal:invite'
          )
        ON CONFLICT (role_id, permission_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions rp USING permissions p
        WHERE rp.permission_id = p.id
          AND p.key IN (
            'calendar:personal:access',
            'calendar:personal:create',
            'calendar:personal:invite'
          )
        """
    )
    for key in PERSONAL_PERMISSIONS:
        op.execute(
            sa.text("DELETE FROM permissions WHERE key = :key").bindparams(key=key)
        )
    op.drop_index(
        "idx_org_calendar_event_org_scope_start",
        table_name="organization_calendar_events",
        schema="public",
    )
    op.drop_constraint(
        "ck_org_calendar_event_scope",
        "organization_calendar_events",
        type_="check",
        schema="public",
    )
    op.drop_column("organization_calendar_events", "event_scope", schema="public")
