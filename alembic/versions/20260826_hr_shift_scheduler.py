"""Add composable HR shift scheduler.

Revision ID: 20260826_hr_shift_scheduler
Revises: 20260826_expense_permissions
Create Date: 2026-08-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260826_hr_shift_scheduler"
down_revision = "20260826_expense_permissions"
branch_labels = None
depends_on = None

SCHEDULE_SCHEMA = "scheduling"
ATTENDANCE_SCHEMA = "attendance"
SCHEDULE_PERMISSIONS = {
    "hr:schedule:read": "View shift schedules within authorized scope",
    "hr:schedule:create": "Create draft shift schedules within authorized scope",
    "hr:schedule:update": "Update draft shift schedules within authorized scope",
    "hr:schedule:submit": "Submit shift schedules for approval",
    "hr:schedule:approve": "Approve or reject submitted shift schedules",
    "hr:schedule:publish": "Publish approved shift schedules",
    "hr:schedule:rules": "Manage shift scheduling policies and rules",
    "hr:schedule:override": "Override configured shift scheduling warnings where allowed",
}


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def _add_enum_value(enum_name: str, value: str) -> None:
    op.execute(
        f"ALTER TYPE {SCHEDULE_SCHEMA}.{enum_name} ADD VALUE IF NOT EXISTS '{value}'"
    )


def _drop_constraint_if_exists(table: str, constraint: str, schema: str) -> None:
    op.execute(f"ALTER TABLE {schema}.{table} DROP CONSTRAINT IF EXISTS {constraint}")


def _create_tenant_policy(table: str) -> None:
    op.execute(f"ALTER TABLE {SCHEDULE_SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEDULE_SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        DROP POLICY IF EXISTS {table}_tenant_isolation ON {SCHEDULE_SCHEMA}.{table}
        """
    )
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation
        ON {SCHEDULE_SCHEMA}.{table}
        USING (organization_id = current_setting('app.current_organization_id', true)::uuid)
        WITH CHECK (organization_id = current_setting('app.current_organization_id', true)::uuid)
        """
    )


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEDULE_SCHEMA}")
    for value in ("SUBMITTED", "APPROVED", "REJECTED"):
        _add_enum_value("schedule_status", value)

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_type t
                JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE n.nspname = 'scheduling'
                  AND t.typname = 'schedule_audit_action'
            ) THEN
                CREATE TYPE scheduling.schedule_audit_action AS ENUM (
                    'CREATED', 'ASSIGNED', 'MOVED', 'REMOVED', 'SUBMITTED',
                    'APPROVED', 'REJECTED', 'PUBLISHED', 'AMENDED',
                    'POLICY_CHANGED', 'OVERRIDE_RECORDED'
                );
            END IF;
        END $$;
        """
    )

    schedule_status = postgresql.ENUM(
        "DRAFT",
        "SUBMITTED",
        "APPROVED",
        "REJECTED",
        "PUBLISHED",
        "COMPLETED",
        name="schedule_status",
        schema=SCHEDULE_SCHEMA,
        create_type=False,
    )
    audit_action = postgresql.ENUM(
        "CREATED",
        "ASSIGNED",
        "MOVED",
        "REMOVED",
        "SUBMITTED",
        "APPROVED",
        "REJECTED",
        "PUBLISHED",
        "AMENDED",
        "POLICY_CHANGED",
        "OVERRIDE_RECORDED",
        name="schedule_audit_action",
        schema=SCHEDULE_SCHEMA,
        create_type=False,
    )

    op.create_table(
        "work_schedule",
        sa.Column(
            "work_schedule_id",
            _uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", _uuid(), nullable=False),
        sa.Column("department_id", _uuid(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("status", schedule_status, nullable=False, server_default="DRAFT"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("parent_schedule_id", _uuid(), nullable=True),
        sa.Column("submitted_by_id", _uuid(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_id", _uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by_id", _uuid(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_by_id", _uuid(), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("updated_by", sa.String(100), nullable=True),
        sa.Column("created_by_id", _uuid(), nullable=True),
        sa.Column("updated_by_id", _uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(["department_id"], ["hr.department.department_id"]),
        sa.ForeignKeyConstraint(
            ["parent_schedule_id"], ["scheduling.work_schedule.work_schedule_id"]
        ),
        sa.ForeignKeyConstraint(["submitted_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["approved_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["published_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["rejected_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        sa.UniqueConstraint(
            "organization_id",
            "department_id",
            "period_start",
            "period_end",
            "revision",
            name="uq_work_schedule_org_dept_period_revision",
        ),
        schema=SCHEDULE_SCHEMA,
    )
    op.create_index(
        "idx_work_schedule_org_dept_period",
        "work_schedule",
        ["organization_id", "department_id", "period_start", "period_end"],
        schema=SCHEDULE_SCHEMA,
    )
    op.create_index(
        "idx_work_schedule_org_status",
        "work_schedule",
        ["organization_id", "status"],
        schema=SCHEDULE_SCHEMA,
    )

    op.create_table(
        "scheduling_policy",
        sa.Column(
            "scheduling_policy_id",
            _uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", _uuid(), nullable=False),
        sa.Column("department_id", _uuid(), nullable=True),
        sa.Column("rule_key", sa.String(80), nullable=False),
        sa.Column(
            "configuration",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("severity", sa.String(20), nullable=False, server_default="warning"),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("updated_by", sa.String(100), nullable=True),
        sa.Column("created_by_id", _uuid(), nullable=True),
        sa.Column("updated_by_id", _uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(["department_id"], ["hr.department.department_id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        schema=SCHEDULE_SCHEMA,
    )
    op.create_index(
        "idx_scheduling_policy_org_rule",
        "scheduling_policy",
        ["organization_id", "rule_key"],
        schema=SCHEDULE_SCHEMA,
    )

    op.create_table(
        "schedule_audit_event",
        sa.Column(
            "schedule_audit_event_id",
            _uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", _uuid(), nullable=False),
        sa.Column("work_schedule_id", _uuid(), nullable=False),
        sa.Column("actor_id", _uuid(), nullable=True),
        sa.Column("action", audit_action, nullable=False),
        sa.Column("previous_status", sa.String(30), nullable=True),
        sa.Column("new_status", sa.String(30), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "event_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(
            ["work_schedule_id"], ["scheduling.work_schedule.work_schedule_id"]
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["people.id"]),
        schema=SCHEDULE_SCHEMA,
    )
    op.create_index(
        "idx_schedule_audit_schedule",
        "schedule_audit_event",
        ["work_schedule_id", "created_at"],
        schema=SCHEDULE_SCHEMA,
    )

    op.create_table(
        "schedule_notification_log",
        sa.Column(
            "schedule_notification_log_id",
            _uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", _uuid(), nullable=False),
        sa.Column("work_schedule_id", _uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("employee_id", _uuid(), nullable=False),
        sa.Column("notification_id", _uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(
            ["work_schedule_id"], ["scheduling.work_schedule.work_schedule_id"]
        ),
        sa.ForeignKeyConstraint(["employee_id"], ["hr.employee.employee_id"]),
        schema=SCHEDULE_SCHEMA,
    )
    op.create_index(
        "uq_schedule_notification_revision_employee",
        "schedule_notification_log",
        ["organization_id", "work_schedule_id", "revision", "employee_id"],
        unique=True,
        schema=SCHEDULE_SCHEMA,
    )

    for table in (
        "work_schedule",
        "scheduling_policy",
        "schedule_audit_event",
        "schedule_notification_log",
    ):
        _create_tenant_policy(table)

    op.add_column(
        "shift_schedule",
        sa.Column("work_schedule_id", _uuid(), nullable=True),
        schema=SCHEDULE_SCHEMA,
    )
    op.add_column(
        "shift_schedule",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        schema=SCHEDULE_SCHEMA,
    )
    op.create_foreign_key(
        "fk_shift_schedule_work_schedule",
        "shift_schedule",
        "work_schedule",
        ["work_schedule_id"],
        ["work_schedule_id"],
        source_schema=SCHEDULE_SCHEMA,
        referent_schema=SCHEDULE_SCHEMA,
    )
    op.create_index(
        "ix_scheduling_shift_schedule_work_schedule_id",
        "shift_schedule",
        ["work_schedule_id"],
        schema=SCHEDULE_SCHEMA,
    )
    _drop_constraint_if_exists(
        "shift_schedule", "uq_shift_schedule_emp_date", SCHEDULE_SCHEMA
    )
    op.create_unique_constraint(
        "uq_shift_schedule_emp_date_revision",
        "shift_schedule",
        ["organization_id", "employee_id", "shift_date", "revision"],
        schema=SCHEDULE_SCHEMA,
    )

    op.add_column(
        "attendance",
        sa.Column("shift_schedule_id", _uuid(), nullable=True),
        schema=ATTENDANCE_SCHEMA,
    )
    op.add_column(
        "attendance",
        sa.Column("work_schedule_id", _uuid(), nullable=True),
        schema=ATTENDANCE_SCHEMA,
    )
    op.create_foreign_key(
        "fk_attendance_shift_schedule",
        "attendance",
        "shift_schedule",
        ["shift_schedule_id"],
        ["shift_schedule_id"],
        source_schema=ATTENDANCE_SCHEMA,
        referent_schema=SCHEDULE_SCHEMA,
    )
    op.create_foreign_key(
        "fk_attendance_work_schedule",
        "attendance",
        "work_schedule",
        ["work_schedule_id"],
        ["work_schedule_id"],
        source_schema=ATTENDANCE_SCHEMA,
        referent_schema=SCHEDULE_SCHEMA,
    )
    op.create_index(
        "ix_attendance_attendance_shift_schedule_id",
        "attendance",
        ["shift_schedule_id"],
        schema=ATTENDANCE_SCHEMA,
    )
    op.create_index(
        "ix_attendance_attendance_work_schedule_id",
        "attendance",
        ["work_schedule_id"],
        schema=ATTENDANCE_SCHEMA,
    )

    for key, description in SCHEDULE_PERMISSIONS.items():
        op.execute(
            sa.text(
                """
                INSERT INTO permissions (id, key, description, is_active, created_at, updated_at)
                VALUES (gen_random_uuid(), :key, :description, TRUE, NOW(), NOW())
                ON CONFLICT (key) DO UPDATE
                SET description = EXCLUDED.description,
                    is_active = TRUE,
                    updated_at = NOW()
                """
            ).bindparams(key=key, description=description)
        )

    op.execute(
        """
        INSERT INTO role_permissions (id, role_id, permission_id)
        SELECT gen_random_uuid(), r.id, p.id
        FROM roles r
        CROSS JOIN permissions p
        WHERE lower(r.name) IN ('admin', 'super_admin', 'system_admin', 'hr_manager', 'hr_director')
          AND p.key LIKE 'hr:schedule:%'
        ON CONFLICT (role_id, permission_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions rp
        USING permissions p
        WHERE rp.permission_id = p.id
          AND p.key LIKE 'hr:schedule:%'
        """
    )
    for key in SCHEDULE_PERMISSIONS:
        op.execute(
            sa.text("DELETE FROM permissions WHERE key = :key").bindparams(key=key)
        )

    op.drop_index(
        "ix_attendance_attendance_work_schedule_id",
        table_name="attendance",
        schema=ATTENDANCE_SCHEMA,
    )
    op.drop_index(
        "ix_attendance_attendance_shift_schedule_id",
        table_name="attendance",
        schema=ATTENDANCE_SCHEMA,
    )
    op.drop_constraint(
        "fk_attendance_work_schedule",
        "attendance",
        schema=ATTENDANCE_SCHEMA,
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_attendance_shift_schedule",
        "attendance",
        schema=ATTENDANCE_SCHEMA,
        type_="foreignkey",
    )
    op.drop_column("attendance", "work_schedule_id", schema=ATTENDANCE_SCHEMA)
    op.drop_column("attendance", "shift_schedule_id", schema=ATTENDANCE_SCHEMA)

    op.drop_constraint(
        "uq_shift_schedule_emp_date_revision",
        "shift_schedule",
        schema=SCHEDULE_SCHEMA,
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_shift_schedule_emp_date",
        "shift_schedule",
        ["organization_id", "employee_id", "shift_date"],
        schema=SCHEDULE_SCHEMA,
    )
    op.drop_index(
        "ix_scheduling_shift_schedule_work_schedule_id",
        table_name="shift_schedule",
        schema=SCHEDULE_SCHEMA,
    )
    op.drop_constraint(
        "fk_shift_schedule_work_schedule",
        "shift_schedule",
        schema=SCHEDULE_SCHEMA,
        type_="foreignkey",
    )
    op.drop_column("shift_schedule", "revision", schema=SCHEDULE_SCHEMA)
    op.drop_column("shift_schedule", "work_schedule_id", schema=SCHEDULE_SCHEMA)

    for table in (
        "schedule_notification_log",
        "schedule_audit_event",
        "scheduling_policy",
        "work_schedule",
    ):
        op.execute(
            f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {SCHEDULE_SCHEMA}.{table}"
        )
        op.execute(f"ALTER TABLE {SCHEDULE_SCHEMA}.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {SCHEDULE_SCHEMA}.{table} DISABLE ROW LEVEL SECURITY")
        op.drop_table(table, schema=SCHEDULE_SCHEMA)

    op.execute("DROP TYPE IF EXISTS scheduling.schedule_audit_action")
    # PostgreSQL enum values added to scheduling.schedule_status are intentionally retained.
