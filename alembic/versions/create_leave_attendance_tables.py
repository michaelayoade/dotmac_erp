"""Create Leave and Attendance tables for DotMac People Ops.

Revision ID: create_leave_attendance_tables
Revises: create_payroll_tables
Create Date: 2025-01-20

This migration creates:
- leave.leave_type: Types of leave (annual, sick, etc.)
- leave.holiday_list: Holiday calendars
- leave.holiday: Individual holidays
- leave.leave_allocation: Employee leave balances
- leave.leave_application: Leave requests
- attendance.shift_type: Work shift definitions
- attendance.attendance: Daily attendance records
"""

import sqlalchemy as sa
from alembic import op
from app.alembic_utils import ensure_enum
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "create_leave_attendance_tables"
down_revision = "create_payroll_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ========================================
    # Create schemas
    # ========================================
    op.execute("CREATE SCHEMA IF NOT EXISTS leave")
    op.execute("CREATE SCHEMA IF NOT EXISTS attendance")

    # ========================================
    # Create enums
    # ========================================
    bind = op.get_bind()
    ensure_enum(bind, "leave_type_policy", "ANNUAL", "MONTHLY", "EARNED", "UNLIMITED")
    ensure_enum(
        bind,
        "leave_application_status",
        "DRAFT",
        "SUBMITTED",
        "APPROVED",
        "REJECTED",
        "CANCELLED",
    )
    ensure_enum(
        bind,
        "attendance_status",
        "PRESENT",
        "ABSENT",
        "HALF_DAY",
        "ON_LEAVE",
        "HOLIDAY",
        "WORK_FROM_HOME",
    )

    # ========================================
    # Leave Type
    # ========================================
    op.create_table(
        "leave_type",
        sa.Column(
            "leave_type_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("leave_type_code", sa.String(30), nullable=False),
        sa.Column("leave_type_name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "allocation_policy",
            postgresql.ENUM(
                "ANNUAL",
                "MONTHLY",
                "EARNED",
                "UNLIMITED",
                name="leave_type_policy",
                create_type=False,
            ),
            nullable=False,
            server_default="ANNUAL",
        ),
        sa.Column("max_days_per_year", sa.Numeric(5, 1), nullable=True),
        sa.Column("max_continuous_days", sa.Integer, nullable=True),
        sa.Column(
            "allow_carry_forward", sa.Boolean, nullable=False, server_default="false"
        ),
        sa.Column("max_carry_forward_days", sa.Numeric(5, 1), nullable=True),
        sa.Column("carry_forward_expiry_months", sa.Integer, nullable=True),
        sa.Column(
            "allow_encashment", sa.Boolean, nullable=False, server_default="false"
        ),
        sa.Column("encashment_threshold_days", sa.Numeric(5, 1), nullable=True),
        sa.Column("is_lwp", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "is_compensatory", sa.Boolean, nullable=False, server_default="false"
        ),
        sa.Column(
            "include_holidays", sa.Boolean, nullable=False, server_default="false"
        ),
        sa.Column(
            "applicable_after_days", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column("is_optional", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("max_optional_leaves", sa.Integer, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("erpnext_id", sa.String(255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("leave_type_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        sa.UniqueConstraint(
            "organization_id", "leave_type_code", name="uq_leave_type_org_code"
        ),
        schema="leave",
    )
    op.create_index(
        "idx_leave_type_org", "leave_type", ["organization_id"], schema="leave"
    )
    op.create_index(
        "idx_leave_type_active",
        "leave_type",
        ["organization_id", "is_active"],
        schema="leave",
    )

    # ========================================
    # Holiday List
    # ========================================
    op.create_table(
        "holiday_list",
        sa.Column(
            "holiday_list_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("list_code", sa.String(30), nullable=False),
        sa.Column("list_name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("year", sa.Integer, nullable=False),
        sa.Column("from_date", sa.Date, nullable=False),
        sa.Column("to_date", sa.Date, nullable=False),
        sa.Column(
            "weekly_off",
            sa.String(50),
            nullable=False,
            server_default="Saturday,Sunday",
        ),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("erpnext_id", sa.String(255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("holiday_list_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        sa.UniqueConstraint(
            "organization_id", "list_code", name="uq_holiday_list_org_code"
        ),
        schema="leave",
    )
    op.create_index(
        "idx_holiday_list_org", "holiday_list", ["organization_id"], schema="leave"
    )
    op.create_index(
        "idx_holiday_list_year",
        "holiday_list",
        ["organization_id", "year"],
        schema="leave",
    )

    # ========================================
    # Holiday
    # ========================================
    op.create_table(
        "holiday",
        sa.Column(
            "holiday_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("holiday_list_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("holiday_date", sa.Date, nullable=False),
        sa.Column("holiday_name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column(
            "is_public_holiday", sa.Boolean, nullable=False, server_default="true"
        ),
        sa.Column("is_optional", sa.Boolean, nullable=False, server_default="false"),
        sa.PrimaryKeyConstraint("holiday_id"),
        sa.ForeignKeyConstraint(
            ["holiday_list_id"],
            ["leave.holiday_list.holiday_list_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "holiday_list_id", "holiday_date", name="uq_holiday_list_date"
        ),
        schema="leave",
    )
    op.create_index(
        "idx_holiday_date",
        "holiday",
        ["holiday_list_id", "holiday_date"],
        schema="leave",
    )

    # ========================================
    # Leave Allocation
    # ========================================
    op.create_table(
        "leave_allocation",
        sa.Column(
            "allocation_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("leave_type_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_date", sa.Date, nullable=False),
        sa.Column("to_date", sa.Date, nullable=False),
        sa.Column(
            "new_leaves_allocated", sa.Numeric(5, 1), nullable=False, server_default="0"
        ),
        sa.Column(
            "carry_forward_leaves", sa.Numeric(5, 1), nullable=False, server_default="0"
        ),
        sa.Column("total_leaves_allocated", sa.Numeric(5, 1), nullable=False),
        sa.Column("leaves_used", sa.Numeric(5, 1), nullable=False, server_default="0"),
        sa.Column(
            "leaves_encashed", sa.Numeric(5, 1), nullable=False, server_default="0"
        ),
        sa.Column(
            "leaves_expired", sa.Numeric(5, 1), nullable=False, server_default="0"
        ),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("erpnext_id", sa.String(255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("allocation_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(["employee_id"], ["hr.employee.employee_id"]),
        sa.ForeignKeyConstraint(["leave_type_id"], ["leave.leave_type.leave_type_id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        sa.UniqueConstraint(
            "employee_id",
            "leave_type_id",
            "from_date",
            name="uq_leave_allocation_emp_type_period",
        ),
        schema="leave",
    )
    op.create_index(
        "idx_leave_allocation_org",
        "leave_allocation",
        ["organization_id"],
        schema="leave",
    )
    op.create_index(
        "idx_leave_allocation_employee",
        "leave_allocation",
        ["employee_id", "from_date"],
        schema="leave",
    )
    op.create_index(
        "idx_leave_allocation_type",
        "leave_allocation",
        ["leave_type_id", "from_date"],
        schema="leave",
    )

    # ========================================
    # Leave Application
    # ========================================
    op.create_table(
        "leave_application",
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("application_number", sa.String(30), nullable=False, unique=True),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("leave_type_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_date", sa.Date, nullable=False),
        sa.Column("to_date", sa.Date, nullable=False),
        sa.Column("half_day", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("half_day_date", sa.Date, nullable=True),
        sa.Column("total_leave_days", sa.Numeric(5, 1), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("contact_during_leave", sa.String(100), nullable=True),
        sa.Column("address_during_leave", sa.Text, nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "DRAFT",
                "SUBMITTED",
                "APPROVED",
                "REJECTED",
                "CANCELLED",
                name="leave_application_status",
                create_type=False,
            ),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("leave_approver_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column(
            "is_posted_to_payroll", sa.Boolean, nullable=False, server_default="false"
        ),
        sa.Column("salary_slip_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_changed_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("erpnext_id", sa.String(255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("application_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(["employee_id"], ["hr.employee.employee_id"]),
        sa.ForeignKeyConstraint(["leave_type_id"], ["leave.leave_type.leave_type_id"]),
        sa.ForeignKeyConstraint(["leave_approver_id"], ["hr.employee.employee_id"]),
        sa.ForeignKeyConstraint(["approved_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["salary_slip_id"], ["payroll.salary_slip.slip_id"]),
        sa.ForeignKeyConstraint(["status_changed_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        schema="leave",
    )
    op.create_index(
        "idx_leave_app_org", "leave_application", ["organization_id"], schema="leave"
    )
    op.create_index(
        "idx_leave_app_employee",
        "leave_application",
        ["employee_id", "from_date"],
        schema="leave",
    )
    op.create_index(
        "idx_leave_app_status",
        "leave_application",
        ["organization_id", "status"],
        schema="leave",
    )
    op.create_index(
        "idx_leave_app_dates",
        "leave_application",
        ["organization_id", "from_date", "to_date"],
        schema="leave",
    )

    # ========================================
    # Shift Type
    # ========================================
    op.create_table(
        "shift_type",
        sa.Column(
            "shift_type_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("shift_code", sa.String(30), nullable=False),
        sa.Column("shift_name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("start_time", sa.Time, nullable=False),
        sa.Column("end_time", sa.Time, nullable=False),
        sa.Column("working_hours", sa.Numeric(4, 2), nullable=False),
        sa.Column(
            "late_entry_grace_period", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column(
            "early_exit_grace_period", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column("enable_half_day", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("half_day_threshold_hours", sa.Numeric(4, 2), nullable=True),
        sa.Column(
            "enable_overtime", sa.Boolean, nullable=False, server_default="false"
        ),
        sa.Column("overtime_threshold_hours", sa.Numeric(4, 2), nullable=True),
        sa.Column(
            "break_duration_minutes", sa.Integer, nullable=False, server_default="60"
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("erpnext_id", sa.String(255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("shift_type_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        sa.UniqueConstraint(
            "organization_id", "shift_code", name="uq_shift_type_org_code"
        ),
        schema="attendance",
    )
    op.create_index(
        "idx_shift_type_org", "shift_type", ["organization_id"], schema="attendance"
    )
    op.create_index(
        "idx_shift_type_active",
        "shift_type",
        ["organization_id", "is_active"],
        schema="attendance",
    )

    # ========================================
    # Attendance
    # ========================================
    op.create_table(
        "attendance",
        sa.Column(
            "attendance_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("shift_type_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("attendance_date", sa.Date, nullable=False),
        sa.Column("check_in", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_out", sa.DateTime(timezone=True), nullable=True),
        sa.Column("working_hours", sa.Numeric(5, 2), nullable=True),
        sa.Column(
            "overtime_hours", sa.Numeric(5, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "PRESENT",
                "ABSENT",
                "HALF_DAY",
                "ON_LEAVE",
                "HOLIDAY",
                "WORK_FROM_HOME",
                name="attendance_status",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("late_entry", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("late_entry_minutes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("early_exit", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("early_exit_minutes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("leave_application_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("remarks", sa.Text, nullable=True),
        sa.Column("marked_by", sa.String(20), nullable=False, server_default="MANUAL"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("erpnext_id", sa.String(255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("attendance_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(["employee_id"], ["hr.employee.employee_id"]),
        sa.ForeignKeyConstraint(
            ["shift_type_id"], ["attendance.shift_type.shift_type_id"]
        ),
        sa.ForeignKeyConstraint(
            ["leave_application_id"], ["leave.leave_application.application_id"]
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        sa.UniqueConstraint(
            "employee_id", "attendance_date", name="uq_attendance_emp_date"
        ),
        schema="attendance",
    )
    op.create_index(
        "idx_attendance_org", "attendance", ["organization_id"], schema="attendance"
    )
    op.create_index(
        "idx_attendance_date",
        "attendance",
        ["organization_id", "attendance_date"],
        schema="attendance",
    )
    op.create_index(
        "idx_attendance_employee",
        "attendance",
        ["employee_id", "attendance_date"],
        schema="attendance",
    )
    op.create_index(
        "idx_attendance_status",
        "attendance",
        ["organization_id", "status", "attendance_date"],
        schema="attendance",
    )

    # ========================================
    # RLS Policies
    # ========================================
    for table, schema in [
        ("leave_type", "leave"),
        ("holiday_list", "leave"),
        ("leave_allocation", "leave"),
        ("leave_application", "leave"),
        ("shift_type", "attendance"),
        ("attendance", "attendance"),
    ]:
        op.execute(f"ALTER TABLE {schema}.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {schema}.{table} FORCE ROW LEVEL SECURITY")

        for action in ["SELECT", "INSERT", "UPDATE", "DELETE"]:
            policy_name = f"{table}_tenant_isolation_{action.lower()}"
            if action == "INSERT":
                op.execute(f"""
                    CREATE POLICY {policy_name} ON {schema}.{table}
                    FOR {action}
                    WITH CHECK (should_bypass_rls() OR organization_id = get_current_organization_id())
                """)
            else:
                op.execute(f"""
                    CREATE POLICY {policy_name} ON {schema}.{table}
                    FOR {action}
                    USING (should_bypass_rls() OR organization_id = get_current_organization_id())
                """)

    # Holiday table uses holiday_list_id, so we need a different RLS approach
    op.execute("ALTER TABLE leave.holiday ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE leave.holiday FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY holiday_tenant_isolation_select ON leave.holiday
        FOR SELECT
        USING (
            should_bypass_rls() OR
            holiday_list_id IN (
                SELECT holiday_list_id FROM leave.holiday_list
                WHERE organization_id = get_current_organization_id()
            )
        )
    """)
    op.execute("""
        CREATE POLICY holiday_tenant_isolation_insert ON leave.holiday
        FOR INSERT
        WITH CHECK (
            should_bypass_rls() OR
            holiday_list_id IN (
                SELECT holiday_list_id FROM leave.holiday_list
                WHERE organization_id = get_current_organization_id()
            )
        )
    """)
    op.execute("""
        CREATE POLICY holiday_tenant_isolation_update ON leave.holiday
        FOR UPDATE
        USING (
            should_bypass_rls() OR
            holiday_list_id IN (
                SELECT holiday_list_id FROM leave.holiday_list
                WHERE organization_id = get_current_organization_id()
            )
        )
    """)
    op.execute("""
        CREATE POLICY holiday_tenant_isolation_delete ON leave.holiday
        FOR DELETE
        USING (
            should_bypass_rls() OR
            holiday_list_id IN (
                SELECT holiday_list_id FROM leave.holiday_list
                WHERE organization_id = get_current_organization_id()
            )
        )
    """)


def downgrade() -> None:
    # Drop tables in reverse order
    op.drop_table("attendance", schema="attendance")
    op.drop_table("shift_type", schema="attendance")
    op.drop_table("leave_application", schema="leave")
    op.drop_table("leave_allocation", schema="leave")
    op.drop_table("holiday", schema="leave")
    op.drop_table("holiday_list", schema="leave")
    op.drop_table("leave_type", schema="leave")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS attendance_status")
    op.execute("DROP TYPE IF EXISTS leave_application_status")
    op.execute("DROP TYPE IF EXISTS leave_type_policy")

    # Drop schemas (only if empty)
    op.execute("DROP SCHEMA IF EXISTS attendance")
    op.execute("DROP SCHEMA IF EXISTS leave")
