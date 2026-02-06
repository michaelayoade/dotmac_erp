"""Add shift scheduling tables.

Revision ID: 20260128_scheduling
Revises:
Create Date: 2026-01-28

Creates tables for automated shift scheduling:
- shift_pattern: Defines weekly shift patterns (day/night/rotating)
- shift_pattern_assignment: Links employees to patterns
- shift_schedule: Generated monthly shift schedules per employee
- shift_swap_request: Employee shift swap requests with approval workflow
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260128_scheduling"
down_revision = "20260128_discipline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the scheduling schema
    op.execute("CREATE SCHEMA IF NOT EXISTS scheduling")

    # Create enums in scheduling schema
    op.execute(
        """
        CREATE TYPE scheduling.rotation_type AS ENUM (
            'DAY_ONLY', 'NIGHT_ONLY', 'ROTATING'
        )
        """
    )
    op.execute(
        """
        CREATE TYPE scheduling.schedule_status AS ENUM (
            'DRAFT', 'PUBLISHED', 'COMPLETED'
        )
        """
    )
    op.execute(
        """
        CREATE TYPE scheduling.swap_request_status AS ENUM (
            'PENDING', 'TARGET_ACCEPTED', 'APPROVED', 'REJECTED', 'CANCELLED'
        )
        """
    )

    # Create shift_pattern table
    op.create_table(
        "shift_pattern",
        sa.Column(
            "shift_pattern_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("pattern_code", sa.String(30), nullable=False),
        sa.Column("pattern_name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "rotation_type",
            postgresql.ENUM(
                "DAY_ONLY",
                "NIGHT_ONLY",
                "ROTATING",
                name="rotation_type",
                schema="scheduling",
                create_type=False,
            ),
            nullable=False,
            server_default="DAY_ONLY",
        ),
        sa.Column("cycle_weeks", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "work_days",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default='["MON","TUE","WED","THU","FRI"]',
        ),
        sa.Column(
            "day_shift_type_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "night_shift_type_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("updated_by", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("shift_pattern_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["core_org.organization.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["day_shift_type_id"],
            ["attendance.shift_type.shift_type_id"],
        ),
        sa.ForeignKeyConstraint(
            ["night_shift_type_id"],
            ["attendance.shift_type.shift_type_id"],
        ),
        sa.UniqueConstraint(
            "organization_id",
            "pattern_code",
            name="uq_shift_pattern_org_code",
        ),
        schema="scheduling",
    )
    op.create_index(
        "idx_shift_pattern_org_active",
        "shift_pattern",
        ["organization_id", "is_active"],
        schema="scheduling",
    )

    # Create shift_pattern_assignment table
    op.create_table(
        "shift_pattern_assignment",
        sa.Column(
            "pattern_assignment_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("shift_pattern_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "rotation_week_offset",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("updated_by", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("pattern_assignment_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["core_org.organization.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["hr.employee.employee_id"],
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["hr.department.department_id"],
        ),
        sa.ForeignKeyConstraint(
            ["shift_pattern_id"],
            ["scheduling.shift_pattern.shift_pattern_id"],
        ),
        schema="scheduling",
    )
    op.create_index(
        "idx_pattern_assignment_org_dept",
        "shift_pattern_assignment",
        ["organization_id", "department_id"],
        schema="scheduling",
    )
    op.create_index(
        "idx_pattern_assignment_employee",
        "shift_pattern_assignment",
        ["employee_id", "effective_from"],
        schema="scheduling",
    )
    op.create_index(
        "idx_pattern_assignment_active",
        "shift_pattern_assignment",
        ["organization_id", "is_active"],
        schema="scheduling",
    )

    # Create shift_schedule table
    op.create_table(
        "shift_schedule",
        sa.Column(
            "shift_schedule_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("shift_date", sa.Date(), nullable=False),
        sa.Column("shift_type_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schedule_month", sa.String(7), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "DRAFT",
                "PUBLISHED",
                "COMPLETED",
                name="schedule_status",
                schema="scheduling",
                create_type=False,
            ),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("updated_by", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("shift_schedule_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["core_org.organization.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["hr.employee.employee_id"],
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["hr.department.department_id"],
        ),
        sa.ForeignKeyConstraint(
            ["shift_type_id"],
            ["attendance.shift_type.shift_type_id"],
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["hr.employee.employee_id"],
        ),
        sa.ForeignKeyConstraint(
            ["published_by_id"],
            ["hr.employee.employee_id"],
        ),
        sa.UniqueConstraint(
            "organization_id",
            "employee_id",
            "shift_date",
            name="uq_shift_schedule_emp_date",
        ),
        schema="scheduling",
    )
    op.create_index(
        "idx_shift_schedule_org_month",
        "shift_schedule",
        ["organization_id", "schedule_month"],
        schema="scheduling",
    )
    op.create_index(
        "idx_shift_schedule_dept_date",
        "shift_schedule",
        ["department_id", "shift_date"],
        schema="scheduling",
    )
    op.create_index(
        "idx_shift_schedule_employee_date",
        "shift_schedule",
        ["employee_id", "shift_date"],
        schema="scheduling",
    )

    # Create shift_swap_request table
    op.create_table(
        "shift_swap_request",
        sa.Column(
            "swap_request_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "requester_schedule_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "target_schedule_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("requester_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "PENDING",
                "TARGET_ACCEPTED",
                "APPROVED",
                "REJECTED",
                "CANCELLED",
                name="swap_request_status",
                schema="scheduling",
                create_type=False,
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("target_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("updated_by", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("swap_request_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["core_org.organization.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["requester_schedule_id"],
            ["scheduling.shift_schedule.shift_schedule_id"],
        ),
        sa.ForeignKeyConstraint(
            ["target_schedule_id"],
            ["scheduling.shift_schedule.shift_schedule_id"],
        ),
        sa.ForeignKeyConstraint(
            ["requester_id"],
            ["hr.employee.employee_id"],
        ),
        sa.ForeignKeyConstraint(
            ["target_employee_id"],
            ["hr.employee.employee_id"],
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_id"],
            ["hr.employee.employee_id"],
        ),
        schema="scheduling",
    )
    op.create_index(
        "idx_swap_request_org_status",
        "shift_swap_request",
        ["organization_id", "status"],
        schema="scheduling",
    )
    op.create_index(
        "idx_swap_request_requester",
        "shift_swap_request",
        ["requester_id", "status"],
        schema="scheduling",
    )
    op.create_index(
        "idx_swap_request_target",
        "shift_swap_request",
        ["target_employee_id", "status"],
        schema="scheduling",
    )


def downgrade() -> None:
    # Drop tables in reverse order (foreign key dependencies)
    op.drop_table("shift_swap_request", schema="scheduling")
    op.drop_table("shift_schedule", schema="scheduling")
    op.drop_table("shift_pattern_assignment", schema="scheduling")
    op.drop_table("shift_pattern", schema="scheduling")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS scheduling.swap_request_status")
    op.execute("DROP TYPE IF EXISTS scheduling.schedule_status")
    op.execute("DROP TYPE IF EXISTS scheduling.rotation_type")

    # Drop schema (only if empty)
    op.execute("DROP SCHEMA IF EXISTS scheduling")
