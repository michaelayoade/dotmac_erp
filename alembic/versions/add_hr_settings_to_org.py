"""Add HR settings columns to organization table.

Revision ID: add_hr_settings_to_org
Revises: merge_people_and_customer_credit
Create Date: 2025-01-20

This migration adds HR configuration columns to core_org.organization
to support the People/HR module integration.

Columns added:
- hr_employee_id_format: Employee ID format pattern
- hr_employee_id_prefix: Employee ID prefix
- hr_payroll_frequency: Payroll processing frequency
- hr_leave_year_start_month: Month when leave year starts
- hr_probation_days: Default probation period
- hr_attendance_mode: Attendance marking mode
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "add_hr_settings_to_org"
down_revision = "merge_people_and_customer_credit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def has_column(schema: str, table: str, column: str) -> bool:
        if not inspector.has_table(table, schema=schema):
            return False
        return any(col["name"] == column for col in inspector.get_columns(table, schema=schema))

    # Add HR settings columns to organization table
    if not has_column("core_org", "organization", "hr_employee_id_format"):
        op.add_column(
            "organization",
            sa.Column(
                "hr_employee_id_format",
                sa.String(50),
                nullable=True,
                comment="Employee ID format, e.g. EMP-{YYYY}-{SEQ}",
            ),
            schema="core_org",
        )
    if not has_column("core_org", "organization", "hr_employee_id_prefix"):
        op.add_column(
            "organization",
            sa.Column(
                "hr_employee_id_prefix",
                sa.String(10),
                nullable=True,
                comment="Employee ID prefix, e.g. EMP",
            ),
            schema="core_org",
        )
    if not has_column("core_org", "organization", "hr_payroll_frequency"):
        op.add_column(
            "organization",
            sa.Column(
                "hr_payroll_frequency",
                sa.String(20),
                nullable=True,
                comment="Payroll frequency: MONTHLY, BIWEEKLY, WEEKLY",
            ),
            schema="core_org",
        )
    if not has_column("core_org", "organization", "hr_leave_year_start_month"):
        op.add_column(
            "organization",
            sa.Column(
                "hr_leave_year_start_month",
                sa.Integer(),
                nullable=True,
                comment="Month when leave year starts (1-12)",
            ),
            schema="core_org",
        )
    if not has_column("core_org", "organization", "hr_probation_days"):
        op.add_column(
            "organization",
            sa.Column(
                "hr_probation_days",
                sa.Integer(),
                nullable=True,
                comment="Default probation period in days",
            ),
            schema="core_org",
        )
    if not has_column("core_org", "organization", "hr_attendance_mode"):
        op.add_column(
            "organization",
            sa.Column(
                "hr_attendance_mode",
                sa.String(20),
                nullable=True,
                comment="Attendance mode: MANUAL, BIOMETRIC, GEOFENCED",
            ),
            schema="core_org",
        )


def downgrade() -> None:
    op.drop_column("organization", "hr_attendance_mode", schema="core_org")
    op.drop_column("organization", "hr_probation_days", schema="core_org")
    op.drop_column("organization", "hr_leave_year_start_month", schema="core_org")
    op.drop_column("organization", "hr_payroll_frequency", schema="core_org")
    op.drop_column("organization", "hr_employee_id_prefix", schema="core_org")
    op.drop_column("organization", "hr_employee_id_format", schema="core_org")
