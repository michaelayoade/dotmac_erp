"""Fix annual_rent nullable mismatch in employee_tax_profile.

Revision ID: 20260123_fix_annual_rent_nullable
Revises: 20260123_add_paye_tax_tables
Create Date: 2026-01-23

The initial migration created annual_rent as nullable, but the model
defines it as NOT NULL with a default of 0. This migration fixes the
mismatch by:
1. Setting any NULL values to 0
2. Altering the column to NOT NULL with default 0
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260123_fix_annual_rent_nullable"
down_revision = "20260123_add_paye_tax_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # First, update any existing NULL values to 0
    op.execute(
        """
        UPDATE payroll.employee_tax_profile
        SET annual_rent = 0
        WHERE annual_rent IS NULL
        """
    )

    # Now alter the column to NOT NULL with a default
    op.alter_column(
        "employee_tax_profile",
        "annual_rent",
        existing_type=sa.Numeric(19, 4),
        nullable=False,
        server_default=sa.text("0"),
        schema="payroll",
    )


def downgrade() -> None:
    # Revert to nullable without default
    op.alter_column(
        "employee_tax_profile",
        "annual_rent",
        existing_type=sa.Numeric(19, 4),
        nullable=True,
        server_default=None,
        schema="payroll",
    )
