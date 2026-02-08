"""Add source_bank_account_id to payroll_entry.

Revision ID: 20260130_add_source_bank_account
Revises: 20260130_add_tax_profile_pension
Create Date: 2026-01-30

Adds source bank account reference for:
- Bank upload file generation (debit account)
- Payment processing integration
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260130_add_source_bank_account"
down_revision = "20260130_add_tax_profile_pension"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payroll_entry",
        sa.Column(
            "source_bank_account_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Bank account for salary payments and bank upload exports",
        ),
        schema="payroll",
    )
    op.create_foreign_key(
        "fk_payroll_entry_source_bank_account",
        "payroll_entry",
        "bank_accounts",
        ["source_bank_account_id"],
        ["bank_account_id"],
        source_schema="payroll",
        referent_schema="banking",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_payroll_entry_source_bank_account",
        "payroll_entry",
        schema="payroll",
        type_="foreignkey",
    )
    op.drop_column("payroll_entry", "source_bank_account_id", schema="payroll")
