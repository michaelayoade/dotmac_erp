"""Merge two heads into one.

Revision ID: 20260208_merge_heads
Revises: 20260207_add_categorization_fields, 20260208_add_employee_expense_approver
Create Date: 2026-02-08
"""

# revision identifiers, used by Alembic.
revision = "20260208_merge_heads"
down_revision = (
    "20260207_add_categorization_fields",
    "20260208_add_employee_expense_approver",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
