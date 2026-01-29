"""Add employee_id index to shift_schedule.

Revision ID: 20260128_scheduling_emp_idx
Revises: 20260128_merge_scheduling
Create Date: 2026-01-28

Adds standalone index on employee_id for employee schedule lookups.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260128_scheduling_emp_idx"
down_revision = "20260128_merge_scheduling"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_shift_schedule_employee",
        "shift_schedule",
        ["employee_id"],
        schema="scheduling",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_shift_schedule_employee",
        table_name="shift_schedule",
        schema="scheduling",
    )
