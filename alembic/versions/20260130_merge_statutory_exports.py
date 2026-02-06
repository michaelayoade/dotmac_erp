"""Merge all heads before statutory exports.

Revision ID: 20260130_merge_statutory_exports
Revises: 20260130_add_payroll_number_sequence, 20260130_add_scheduling_audit_ids, 20260130_merge_all, 20260130_payroll_payslip_email_tracking
Create Date: 2026-01-30

Merge all existing heads to enable statutory export migrations.
"""

revision = "20260130_merge_statutory_exports"
down_revision = (
    "20260130_add_payroll_number_sequence",
    "20260130_add_scheduling_audit_ids",
    "20260130_merge_all",
    "20260130_payroll_payslip_email_tracking",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
