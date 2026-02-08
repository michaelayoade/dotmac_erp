"""Add payslip email tracking fields to payroll_entry.

Revision ID: 20260130_payroll_payslip_email_tracking
Revises: e0696f5adbeb
Create Date: 2026-01-30
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260130_payroll_payslip_email_tracking"
down_revision = "e0696f5adbeb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payroll_entry",
        sa.Column("payslips_email_status", sa.String(length=20), nullable=True),
        schema="payroll",
    )
    op.add_column(
        "payroll_entry",
        sa.Column("payslips_email_queued_at", sa.DateTime(), nullable=True),
        schema="payroll",
    )
    op.add_column(
        "payroll_entry",
        sa.Column(
            "payslips_email_queued_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=True,
        ),
        schema="payroll",
    )
    op.add_column(
        "payroll_entry",
        sa.Column("payslips_email_total_count", sa.Integer(), nullable=True),
        schema="payroll",
    )
    op.add_column(
        "payroll_entry",
        sa.Column("payslips_email_processed_count", sa.Integer(), nullable=True),
        schema="payroll",
    )
    op.add_column(
        "payroll_entry",
        sa.Column("payslips_email_error_count", sa.Integer(), nullable=True),
        schema="payroll",
    )
    op.add_column(
        "payroll_entry",
        sa.Column("payslips_email_last_run_at", sa.DateTime(), nullable=True),
        schema="payroll",
    )


def downgrade() -> None:
    op.drop_column("payroll_entry", "payslips_email_last_run_at", schema="payroll")
    op.drop_column("payroll_entry", "payslips_email_error_count", schema="payroll")
    op.drop_column("payroll_entry", "payslips_email_processed_count", schema="payroll")
    op.drop_column("payroll_entry", "payslips_email_total_count", schema="payroll")
    op.drop_column("payroll_entry", "payslips_email_queued_by_id", schema="payroll")
    op.drop_column("payroll_entry", "payslips_email_queued_at", schema="payroll")
    op.drop_column("payroll_entry", "payslips_email_status", schema="payroll")
