"""Add payroll proration fields to organization and salary_slip.

Supports automatic pro-rating for employees who join or leave mid-period.

Revision ID: 20260130_add_payroll_proration
Revises: 20260130_merge_all
Create Date: 2026-01-30
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260130_add_payroll_proration"
down_revision = "20260130_merge_all"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add proration settings to organization
    op.add_column(
        "organization",
        sa.Column(
            "hr_proration_method",
            sa.String(20),
            nullable=True,
            comment="Proration method: CALENDAR_DAYS, BUSINESS_DAYS, FIXED_30_DAY",
        ),
        schema="core_org",
    )
    op.add_column(
        "organization",
        sa.Column(
            "hr_proration_exclude_weekends",
            sa.Boolean(),
            nullable=False,
            server_default="true",
            comment="Exclude weekends in BUSINESS_DAYS proration",
        ),
        schema="core_org",
    )
    op.add_column(
        "organization",
        sa.Column(
            "hr_proration_exclude_holidays",
            sa.Boolean(),
            nullable=False,
            server_default="true",
            comment="Exclude public holidays in BUSINESS_DAYS proration",
        ),
        schema="core_org",
    )

    # Add proration tracking to salary_slip
    op.add_column(
        "salary_slip",
        sa.Column(
            "is_prorated",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment="Whether salary was pro-rated for this period",
        ),
        schema="payroll",
    )
    op.add_column(
        "salary_slip",
        sa.Column(
            "proration_reason",
            sa.String(30),
            nullable=True,
            comment="JOINED_MID_PERIOD, LEFT_MID_PERIOD, or BOTH",
        ),
        schema="payroll",
    )
    op.add_column(
        "salary_slip",
        sa.Column(
            "proration_method",
            sa.String(20),
            nullable=True,
            comment="Method used: CALENDAR_DAYS, BUSINESS_DAYS, FIXED_30_DAY",
        ),
        schema="payroll",
    )
    op.add_column(
        "salary_slip",
        sa.Column(
            "employee_start_in_period",
            sa.Date(),
            nullable=True,
            comment="Actual start date within the period (if pro-rated)",
        ),
        schema="payroll",
    )
    op.add_column(
        "salary_slip",
        sa.Column(
            "employee_end_in_period",
            sa.Date(),
            nullable=True,
            comment="Actual end date within the period (if pro-rated)",
        ),
        schema="payroll",
    )


def downgrade() -> None:
    # Remove salary_slip columns
    op.drop_column("salary_slip", "employee_end_in_period", schema="payroll")
    op.drop_column("salary_slip", "employee_start_in_period", schema="payroll")
    op.drop_column("salary_slip", "proration_method", schema="payroll")
    op.drop_column("salary_slip", "proration_reason", schema="payroll")
    op.drop_column("salary_slip", "is_prorated", schema="payroll")

    # Remove organization columns
    op.drop_column("organization", "hr_proration_exclude_holidays", schema="core_org")
    op.drop_column("organization", "hr_proration_exclude_weekends", schema="core_org")
    op.drop_column("organization", "hr_proration_method", schema="core_org")
