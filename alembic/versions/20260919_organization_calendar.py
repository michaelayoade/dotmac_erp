"""Add the ERP-owned organization calendar foundation.

Revision ID: 20260919_organization_calendar
Revises: 20260919_merge_invoice_sync_push_delivery
Create Date: 2026-09-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260919_organization_calendar"
down_revision = "20260919_merge_invoice_sync_push_delivery"
branch_labels = None
depends_on = None

TABLES = (
    "organization_calendar_events",
    "organization_calendar_participants",
    "organization_calendar_reminders",
    "organization_calendar_audit",
)

PERMISSIONS = {
    "calendar:events:access": "Access the ERP organization calendar",
    "calendar:events:read_assigned": "View organization events assigned to the employee",
    "calendar:events:read_all": "View all organization calendar events",
    "calendar:events:create": "Create organization calendar events",
    "calendar:events:update_own": "Update organization calendar events created by the user",
    "calendar:events:update_all": "Update all organization calendar events",
    "calendar:events:cancel_own": "Cancel organization calendar events created by the user",
    "calendar:events:cancel_all": "Cancel all organization calendar events",
    "calendar:participants:manage": "Select organization calendar participants",
    "calendar:participants:add_all": "Add every eligible employee to an event",
    "calendar:sync:retry": "Retry failed organization calendar synchronization",
    "calendar:audit:read": "Read organization calendar audit history",
}


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "organization_calendar_events",
        sa.Column(
            "event_id",
            _uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", _uuid(), nullable=False),
        sa.Column("ical_uid", sa.String(255), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("event_details", sa.Text()),
        sa.Column("location", sa.String(255)),
        sa.Column("meeting_url", sa.String(1000)),
        sa.Column("color", sa.String(7), nullable=False, server_default="#4F46E5"),
        sa.Column("all_day", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("start_at", sa.DateTime(timezone=True)),
        sa.Column("end_at", sa.DateTime(timezone=True)),
        sa.Column("start_date", sa.Date()),
        sa.Column("end_date_exclusive", sa.Date()),
        sa.Column(
            "timezone", sa.String(64), nullable=False, server_default="Africa/Lagos"
        ),
        sa.Column(
            "business_status", sa.String(20), nullable=False, server_default="DRAFT"
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by_id", _uuid(), nullable=False),
        sa.Column("updated_by_id", _uuid(), nullable=False),
        sa.Column("published_by_id", _uuid()),
        sa.Column("cancelled_by_id", _uuid()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "(all_day = false AND start_at IS NOT NULL AND end_at IS NOT NULL AND start_date IS NULL AND end_date_exclusive IS NULL) OR "
            "(all_day = true AND start_date IS NOT NULL AND end_date_exclusive IS NOT NULL AND start_at IS NULL AND end_at IS NULL)",
            name="ck_org_calendar_event_time_shape",
        ),
        sa.CheckConstraint(
            "(all_day = false AND end_at > start_at) OR (all_day = true AND end_date_exclusive > start_date)",
            name="ck_org_calendar_event_positive_duration",
        ),
        sa.CheckConstraint("version >= 1", name="ck_org_calendar_event_version"),
        sa.CheckConstraint(
            "business_status IN ('DRAFT', 'PUBLISHED', 'CANCELLED')",
            name="ck_org_calendar_event_business_status",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["published_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["cancelled_by_id"], ["people.id"]),
        sa.UniqueConstraint("ical_uid", name="uq_org_calendar_event_ical_uid"),
        schema="public",
    )
    op.create_index(
        "idx_org_calendar_event_org_start",
        "organization_calendar_events",
        ["organization_id", "start_at", "start_date"],
        schema="public",
    )
    op.create_index(
        "idx_org_calendar_event_org_status",
        "organization_calendar_events",
        ["organization_id", "business_status"],
        schema="public",
    )

    op.create_table(
        "organization_calendar_participants",
        sa.Column(
            "participant_id",
            _uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", _uuid(), nullable=False),
        sa.Column("event_id", _uuid(), nullable=False),
        sa.Column("person_id", _uuid(), nullable=False),
        sa.Column("employee_id", _uuid(), nullable=False),
        sa.Column("participant_name", sa.String(160), nullable=False),
        sa.Column("participant_email", sa.String(255), nullable=False),
        sa.Column("nextcloud_user_id", sa.String(255), nullable=False),
        sa.Column(
            "identity_status", sa.String(32), nullable=False, server_default="VERIFIED"
        ),
        sa.Column(
            "membership_status", sa.String(20), nullable=False, server_default="ACTIVE"
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "identity_status IN ('VERIFIED', 'MISSING_NEXTCLOUD_ACCOUNT', 'MISSING_ERP_MAPPING', 'EMAIL_MISMATCH', 'DUPLICATE_IDENTITY', 'DISABLED', 'UNRESOLVED')",
            name="ck_org_calendar_participant_identity_status",
        ),
        sa.CheckConstraint(
            "membership_status IN ('ACTIVE', 'REMOVED', 'CANCELLED')",
            name="ck_org_calendar_participant_membership_status",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["public.organization_calendar_events.event_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["employee_id"], ["hr.employee.employee_id"]),
        sa.UniqueConstraint(
            "event_id", "person_id", name="uq_org_calendar_participant_event_person"
        ),
        schema="public",
    )
    op.create_index(
        "idx_org_calendar_participant_org_event",
        "organization_calendar_participants",
        ["organization_id", "event_id"],
        schema="public",
    )
    op.create_index(
        "idx_org_calendar_participant_person",
        "organization_calendar_participants",
        ["organization_id", "person_id", "membership_status"],
        schema="public",
    )

    op.create_table(
        "organization_calendar_reminders",
        sa.Column(
            "reminder_id",
            _uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", _uuid(), nullable=False),
        sa.Column("event_id", _uuid(), nullable=False),
        sa.Column("offset_minutes", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "offset_minutes >= 0", name="ck_org_calendar_reminder_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["public.organization_calendar_events.event_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "event_id", "offset_minutes", name="uq_org_calendar_reminder_offset"
        ),
        schema="public",
    )
    op.create_index(
        "idx_org_calendar_reminder_org_event",
        "organization_calendar_reminders",
        ["organization_id", "event_id"],
        schema="public",
    )

    op.create_table(
        "organization_calendar_audit",
        sa.Column(
            "audit_id",
            _uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", _uuid(), nullable=False),
        sa.Column("event_id", _uuid(), nullable=False),
        sa.Column("actor_person_id", _uuid(), nullable=True),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("old_values", postgresql.JSONB()),
        sa.Column("new_values", postgresql.JSONB()),
        sa.Column("correlation_id", sa.String(100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["public.organization_calendar_events.event_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["actor_person_id"], ["people.id"]),
        schema="public",
    )
    op.create_index(
        "idx_org_calendar_audit_org_event",
        "organization_calendar_audit",
        ["organization_id", "event_id"],
        schema="public",
    )

    for table in TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON public.{table}
            USING (organization_id = current_setting('app.current_organization_id')::uuid)
            WITH CHECK (organization_id = current_setting('app.current_organization_id')::uuid)
            """
        )
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.{table} TO app_user"
        )

    for key, description in PERMISSIONS.items():
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
        WHERE lower(r.name) IN ('admin', 'super_admin', 'system_admin')
          AND p.key LIKE 'calendar:%'
        ON CONFLICT (role_id, permission_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO role_permissions (id, role_id, permission_id)
        SELECT gen_random_uuid(), r.id, p.id
        FROM roles r CROSS JOIN permissions p
        WHERE lower(r.name) IN ('finance_manager', 'finance_director')
          AND p.key IN (
            'calendar:events:access', 'calendar:events:read_all',
            'calendar:events:create', 'calendar:events:update_own',
            'calendar:events:cancel_own', 'calendar:participants:manage',
            'calendar:participants:add_all'
          )
        ON CONFLICT (role_id, permission_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO role_permissions (id, role_id, permission_id)
        SELECT gen_random_uuid(), r.id, p.id
        FROM roles r CROSS JOIN permissions p
        WHERE lower(r.name) = 'employee'
          AND p.key = 'calendar:events:read_assigned'
        ON CONFLICT (role_id, permission_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions rp USING permissions p
        WHERE rp.permission_id = p.id AND p.key LIKE 'calendar:%'
        """
    )
    for key in PERMISSIONS:
        op.execute(
            sa.text("DELETE FROM permissions WHERE key = :key").bindparams(key=key)
        )
    for table in reversed(TABLES):
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM app_user")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON public.{table}")
        op.drop_table(table, schema="public")
