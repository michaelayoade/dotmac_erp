"""add_payroll_entry_bank_account

Revision ID: 20260131_add_payroll_entry_bank_account
Revises: e0696f5adbeb
Create Date: 2026-01-31

"""

from alembic import op
import sqlalchemy as sa


revision = "20260131_add_payroll_entry_bank_account"
down_revision = "e0696f5adbeb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payroll_entry",
        sa.Column("bank_account_id", sa.UUID(as_uuid=True), nullable=True),
        schema="payroll",
    )
    op.create_foreign_key(
        "fk_payroll_entry_bank_account",
        "payroll_entry",
        "bank_accounts",
        ["bank_account_id"],
        ["bank_account_id"],
        source_schema="payroll",
        referent_schema="banking",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_payroll_entry_bank_account",
        "payroll_entry",
        schema="payroll",
        type_="foreignkey",
    )
    op.drop_column("payroll_entry", "bank_account_id", schema="payroll")
