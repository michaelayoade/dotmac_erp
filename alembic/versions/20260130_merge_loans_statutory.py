"""Merge all branches before final head.

Revision ID: 20260130_merge_loans_statutory
Revises: 20260130_add_payslip_branding_options, 20260130_add_source_bank_account
Create Date: 2026-01-30

Note: Employee loans migration skipped temporarily - will be added later.
"""
from alembic import op


revision = "20260130_merge_loans_statutory"
down_revision = (
    "20260130_add_payslip_branding_options",
    "20260130_add_source_bank_account",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
