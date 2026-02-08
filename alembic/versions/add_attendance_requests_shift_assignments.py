"""Add attendance requests and shift assignments.

Revision ID: add_attendance_requests_shift_assignments
Revises: create_leave_attendance_tables
Create Date: 2025-02-01

Adds:
- attendance.shift_assignment
- attendance.attendance_request
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.alembic_utils import ensure_enum

# revision identifiers, used by Alembic.
revision = "add_attendance_requests_shift_assignments"
down_revision = "create_leave_attendance_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    ensure_enum(
        bind,
        "attendance_request_status",
        "DRAFT",
        "PENDING",
        "APPROVED",
        "REJECTED",
    )

    op.create_table(
        "shift_assignment",
        sa.Column(
            "shift_assignment_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("shift_type_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("erpnext_id", sa.String(length=255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["hr.employee.employee_id"],
        ),
        sa.ForeignKeyConstraint(
            ["shift_type_id"],
            ["attendance.shift_type.shift_type_id"],
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["core_org.organization.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["people.id"],
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["people.id"],
        ),
        sa.PrimaryKeyConstraint("shift_assignment_id"),
        schema="attendance",
    )
    op.create_index(
        "idx_shift_assignment_org",
        "shift_assignment",
        ["organization_id"],
        schema="attendance",
    )
    op.create_index(
        "idx_shift_assignment_employee",
        "shift_assignment",
        ["employee_id", "start_date"],
        schema="attendance",
    )
    op.create_index(
        "idx_shift_assignment_shift_type",
        "shift_assignment",
        ["shift_type_id"],
        schema="attendance",
    )

    op.create_table(
        "attendance_request",
        sa.Column(
            "attendance_request_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_date", sa.Date(), nullable=False),
        sa.Column("to_date", sa.Date(), nullable=False),
        sa.Column(
            "half_day", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("half_day_date", sa.Date(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "DRAFT",
                "PENDING",
                "APPROVED",
                "REJECTED",
                name="attendance_request_status",
                create_type=False,
            ),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("erpnext_id", sa.String(length=255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_changed_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["hr.employee.employee_id"],
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["core_org.organization.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["people.id"],
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["people.id"],
        ),
        sa.ForeignKeyConstraint(
            ["status_changed_by_id"],
            ["people.id"],
        ),
        sa.PrimaryKeyConstraint("attendance_request_id"),
        schema="attendance",
    )
    op.create_index(
        "idx_attendance_request_org",
        "attendance_request",
        ["organization_id"],
        schema="attendance",
    )
    op.create_index(
        "idx_attendance_request_employee",
        "attendance_request",
        ["employee_id", "from_date"],
        schema="attendance",
    )
    op.create_index(
        "idx_attendance_request_status",
        "attendance_request",
        ["organization_id", "status"],
        schema="attendance",
    )
    op.create_index(
        "idx_attendance_request_dates",
        "attendance_request",
        ["organization_id", "from_date", "to_date"],
        schema="attendance",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_attendance_request_dates",
        table_name="attendance_request",
        schema="attendance",
    )
    op.drop_index(
        "idx_attendance_request_status",
        table_name="attendance_request",
        schema="attendance",
    )
    op.drop_index(
        "idx_attendance_request_employee",
        table_name="attendance_request",
        schema="attendance",
    )
    op.drop_index(
        "idx_attendance_request_org",
        table_name="attendance_request",
        schema="attendance",
    )
    op.drop_table("attendance_request", schema="attendance")

    op.drop_index(
        "idx_shift_assignment_shift_type",
        table_name="shift_assignment",
        schema="attendance",
    )
    op.drop_index(
        "idx_shift_assignment_employee",
        table_name="shift_assignment",
        schema="attendance",
    )
    op.drop_index(
        "idx_shift_assignment_org", table_name="shift_assignment", schema="attendance"
    )
    op.drop_table("shift_assignment", schema="attendance")

    op.execute("DROP TYPE IF EXISTS attendance_request_status")
