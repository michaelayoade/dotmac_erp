"""merge_paystack_and_staging_heads

Revision ID: 24f0f6d22a8c
Revises: 20260123_add_employee_location_shift_fields, add_paystack_payment_tables, create_staging_tables
Create Date: 2026-01-23 13:12:13.621300

"""

revision = "24f0f6d22a8c"
down_revision = (
    "20260123_add_employee_location_shift_fields",
    "add_paystack_payment_tables",
    "create_staging_tables",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
