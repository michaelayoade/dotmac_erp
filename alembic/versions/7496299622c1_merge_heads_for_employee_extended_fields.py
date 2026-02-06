"""merge_heads_for_employee_extended_fields

Revision ID: 7496299622c1
Revises: 20260125_add_employee_extended_tables, add_scheduler_crontab, 20260127_add_salary_slip_bank_branch_code
Create Date: 2026-01-27 16:21:30.094160

"""

revision = "7496299622c1"
down_revision = (
    "20260125_add_employee_extended_tables",
    "add_scheduler_crontab",
    "20260127_add_salary_slip_bank_branch_code",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
