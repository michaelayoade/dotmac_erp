"""Add new numbering sequence types for unified numbering system.

Adds CUSTOMER, EMPLOYEE, LEAVE_APPLICATION, SALARY_SLIP,
PAYROLL_ENTRY, and EXPENSE_INVOICE to the sequence_type enum.

Revision ID: 20260208_add_numbering_sequence_types
Revises: 20260207_expense_claim_composite_unique
Create Date: 2026-02-08
"""

from alembic import op

revision = "20260208_add_numbering_sequence_types"
down_revision = "20260207_expense_claim_composite_unique"
branch_labels = None
depends_on = None

NEW_VALUES = [
    "CUSTOMER",
    "EMPLOYEE",
    "LEAVE_APPLICATION",
    "SALARY_SLIP",
    "PAYROLL_ENTRY",
    "EXPENSE_INVOICE",
]


def upgrade() -> None:
    # Enum value additions must be committed before use in subsequent statements.
    # Use autocommit block to avoid "unsafe use of new value" errors.
    ctx = op.get_context()
    with ctx.autocommit_block():
        for value in NEW_VALUES:
            op.execute(f"ALTER TYPE sequence_type ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values.
    # These values are harmless if left in place.
    pass
