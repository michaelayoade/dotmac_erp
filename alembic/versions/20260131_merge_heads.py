"""Merge migration heads.

Revision ID: 20260131_merge_heads
Revises: 20260130_add_employee_loans, 20260130_add_info_change_request
Create Date: 2026-01-31
"""

revision = "20260131_merge_heads"
down_revision = ("20260130_add_employee_loans", "20260130_add_info_change_request")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
