"""Create People (HR/HRIS) schemas for DotMac People Ops integration.

Revision ID: create_people_schemas
Revises: add_customer_credit_hold
Create Date: 2025-01-20

This migration creates PostgreSQL schemas for the People/HR modules
that will integrate with the Finance system.

Schemas:
- hr: Core HR (departments, designations, employees, grades)
- payroll: Payroll processing (components, structures, slips)
- leave: Leave management (types, allocations, applications)
- attendance: Attendance tracking (shifts, attendance records)
- recruit: Recruitment (jobs, applicants, interviews, offers)
- training: Training management (programs, events, results)
- perf: Performance management (KPIs, appraisals, scorecards)
- migration: Temporary schema for data migration mapping tables
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "create_people_schemas"
down_revision = "add_version_columns"
branch_labels = None
depends_on = None

# People/HR schemas
PEOPLE_SCHEMAS = [
    "hr",
    "payroll",
    "leave",
    "attendance",
    "recruit",
    "training",
    "perf",
    "migration",
]


def upgrade() -> None:
    # Create all People schemas
    for schema in PEOPLE_SCHEMAS:
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")


def downgrade() -> None:
    # Drop all People schemas (in reverse order)
    for schema in reversed(PEOPLE_SCHEMAS):
        op.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
