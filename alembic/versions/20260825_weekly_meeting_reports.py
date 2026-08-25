"""Add tenant-scoped weekly meeting reports.

Revision ID: 20260825_weekly_meeting_reports
Revises: 20260824_outbox_relay
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260825_weekly_meeting_reports"
down_revision = "20260824_outbox_relay"
branch_labels = None
depends_on = None

SCHEMA = "perf"
TABLES = (
    "weekly_meeting_report",
    "weekly_meeting_participant",
    "weekly_meeting_action_item",
)

PERMISSIONS = {
    "performance:weekly_reports:read": "View weekly meeting reports",
    "performance:weekly_reports:write": "Create and edit weekly meeting report drafts",
    "performance:weekly_reports:submit": "Submit weekly meeting reports",
    "performance:weekly_reports:reopen": "Reopen submitted weekly meeting reports",
}


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column(
        "organization",
        sa.Column(
            "hr_weekly_report_email",
            sa.String(length=255),
            nullable=True,
            comment="Recipient for submitted weekly meeting reports",
        ),
        schema="core_org",
    )

    op.create_table(
        "weekly_meeting_report",
        sa.Column(
            "report_id",
            _uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", _uuid(), nullable=False),
        sa.Column("report_number", sa.String(length=80), nullable=False),
        sa.Column("department_id", _uuid(), nullable=False),
        sa.Column("division_name_snapshot", sa.String(length=160), nullable=False),
        sa.Column("division_head_employee_id", _uuid(), nullable=True),
        sa.Column("division_head_name_snapshot", sa.String(length=160), nullable=True),
        sa.Column("week_ending", sa.Date(), nullable=False),
        sa.Column("meeting_date", sa.Date(), nullable=False),
        sa.Column("meeting_time", sa.Time(), nullable=False),
        sa.Column("prepared_by_employee_id", _uuid(), nullable=True),
        sa.Column("prepared_by_person_id", _uuid(), nullable=False),
        sa.Column("prepared_by_name_snapshot", sa.String(length=160), nullable=False),
        sa.Column("purpose_context", sa.Text(), nullable=True),
        sa.Column("matters_discussed", sa.Text(), nullable=True),
        sa.Column("key_decisions", sa.Text(), nullable=True),
        sa.Column("issues_risks_support", sa.Text(), nullable=True),
        sa.Column("carry_forward", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("hr_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_by_id", _uuid(), nullable=True),
        sa.Column("notification_recipient", sa.String(length=255), nullable=True),
        sa.Column(
            "notification_status",
            sa.String(length=20),
            nullable=False,
            server_default="NOT_QUEUED",
        ),
        sa.Column(
            "notification_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("notification_attempted_at", sa.DateTime(timezone=True)),
        sa.Column("notification_sent_at", sa.DateTime(timezone=True)),
        sa.Column("notification_last_error", sa.Text()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by_id", _uuid(), nullable=True),
        sa.Column("updated_by_id", _uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'SUBMITTED')",
            name="ck_weekly_meeting_report_status",
        ),
        sa.CheckConstraint(
            "notification_status IN ('NOT_QUEUED', 'PENDING', 'PROCESSING', 'SENT', 'FAILED')",
            name="ck_weekly_meeting_report_email_status",
        ),
        sa.CheckConstraint(
            "meeting_date <= week_ending",
            name="ck_weekly_meeting_report_date_within_week",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(["department_id"], ["hr.department.department_id"]),
        sa.ForeignKeyConstraint(
            ["division_head_employee_id"], ["hr.employee.employee_id"]
        ),
        sa.ForeignKeyConstraint(
            ["prepared_by_employee_id"], ["hr.employee.employee_id"]
        ),
        sa.ForeignKeyConstraint(["prepared_by_person_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["submitted_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        sa.UniqueConstraint(
            "organization_id",
            "department_id",
            "week_ending",
            name="uq_weekly_meeting_report_org_department_week",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_weekly_meeting_report_org_status_week",
        "weekly_meeting_report",
        ["organization_id", "status", "week_ending"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_perf_weekly_meeting_report_organization_id",
        "weekly_meeting_report",
        ["organization_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "weekly_meeting_participant",
        sa.Column(
            "participant_id",
            _uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", _uuid(), nullable=False),
        sa.Column("report_id", _uuid(), nullable=False),
        sa.Column("employee_id", _uuid(), nullable=True),
        sa.Column("name_snapshot", sa.String(length=160), nullable=False),
        sa.Column("role_snapshot", sa.String(length=160), nullable=True),
        sa.Column(
            "attendance_status",
            sa.String(length=20),
            nullable=False,
            server_default="INVITED",
        ),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column(
            "role_overridden", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by_id", _uuid(), nullable=True),
        sa.Column("updated_by_id", _uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attendance_status IN ('INVITED', 'PRESENT', 'ABSENT', 'EXCUSED')",
            name="ck_weekly_meeting_participant_attendance",
        ),
        sa.CheckConstraint(
            "source IN ('SUGGESTED', 'EMPLOYEE', 'EXTERNAL')",
            name="ck_weekly_meeting_participant_source",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["perf.weekly_meeting_report.report_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["employee_id"], ["hr.employee.employee_id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        sa.UniqueConstraint(
            "report_id",
            "employee_id",
            name="uq_weekly_meeting_participant_report_employee",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_weekly_meeting_participant_org_report",
        "weekly_meeting_participant",
        ["organization_id", "report_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "weekly_meeting_action_item",
        sa.Column(
            "action_item_id",
            _uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", _uuid(), nullable=False),
        sa.Column("report_id", _uuid(), nullable=False),
        sa.Column("action_text", sa.Text(), nullable=False),
        sa.Column("owner_employee_id", _uuid(), nullable=True),
        sa.Column("owner_name_snapshot", sa.String(length=160), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="NOT_STARTED",
        ),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by_id", _uuid(), nullable=True),
        sa.Column("updated_by_id", _uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('NOT_STARTED', 'IN_PROGRESS', 'COMPLETED', 'BLOCKED')",
            name="ck_weekly_meeting_action_status",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["perf.weekly_meeting_report.report_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["owner_employee_id"], ["hr.employee.employee_id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_weekly_meeting_action_org_report",
        "weekly_meeting_action_item",
        ["organization_id", "report_id"],
        schema=SCHEMA,
    )

    for table in TABLES:
        op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {SCHEMA}.{table}
            USING (
                organization_id =
                current_setting('app.current_organization_id')::uuid
            )
            WITH CHECK (
                organization_id =
                current_setting('app.current_organization_id')::uuid
            )
            """
        )

    for key, description in PERMISSIONS.items():
        escaped_description = description.replace("'", "''")
        op.execute(
            f"""
            INSERT INTO permissions (
                id, key, description, is_active, created_at, updated_at
            )
            VALUES (
                gen_random_uuid(), '{key}', '{escaped_description}',
                TRUE, NOW(), NOW()
            )
            ON CONFLICT (key) DO UPDATE
            SET description = EXCLUDED.description,
                is_active = TRUE,
                updated_at = NOW()
            """
        )

    op.execute(
        """
        INSERT INTO role_permissions (id, role_id, permission_id)
        SELECT gen_random_uuid(), r.id, p.id
        FROM roles r
        CROSS JOIN permissions p
        WHERE lower(r.name) IN (
            'admin', 'super_admin', 'system_admin', 'hr_manager', 'hr_director'
        )
          AND p.key LIKE 'performance:weekly_reports:%'
        ON CONFLICT (role_id, permission_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions rp
        USING permissions p
        WHERE rp.permission_id = p.id
          AND p.key LIKE 'performance:weekly_reports:%'
        """
    )
    for key in PERMISSIONS:
        op.execute(
            sa.text("DELETE FROM permissions WHERE key = :key").bindparams(key=key)
        )

    for table in reversed(TABLES):
        op.execute(
            f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {SCHEMA}.{table}"
        )
        op.execute(f"ALTER TABLE {SCHEMA}.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} DISABLE ROW LEVEL SECURITY")
        op.drop_table(table, schema=SCHEMA)

    op.drop_column("organization", "hr_weekly_report_email", schema="core_org")
