"""Add pension/NHF fields to employee_tax_profile.

Revision ID: 20260130_add_tax_profile_pension
Revises: 20260130_add_pfa_directory
Create Date: 2026-01-30

Adds fields for statutory exports:
- rsa_pin: Retirement Savings Account PIN for pension
- pfa_code: Reference to PFA directory
- nhf_number: NHF registration number
"""

import sqlalchemy as sa

from alembic import op

revision = "20260130_add_tax_profile_pension"
down_revision = "20260130_add_pfa_directory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add rsa_pin column
    op.add_column(
        "employee_tax_profile",
        sa.Column(
            "rsa_pin",
            sa.String(20),
            nullable=True,
            comment="Retirement Savings Account PIN",
        ),
        schema="payroll",
    )

    # Add pfa_code column with FK to pfa_directory
    op.add_column(
        "employee_tax_profile",
        sa.Column(
            "pfa_code",
            sa.String(10),
            nullable=True,
            comment="PFA code from pfa_directory",
        ),
        schema="payroll",
    )
    op.create_foreign_key(
        "fk_employee_tax_profile_pfa_code",
        "employee_tax_profile",
        "pfa_directory",
        ["pfa_code"],
        ["pfa_code"],
        source_schema="payroll",
        referent_schema="core_org",
    )

    # Add nhf_number column
    op.add_column(
        "employee_tax_profile",
        sa.Column(
            "nhf_number",
            sa.String(20),
            nullable=True,
            comment="NHF registration number",
        ),
        schema="payroll",
    )

    # Create index on rsa_pin for lookups
    op.create_index(
        "ix_employee_tax_profile_rsa_pin",
        "employee_tax_profile",
        ["organization_id", "rsa_pin"],
        schema="payroll",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_employee_tax_profile_rsa_pin",
        table_name="employee_tax_profile",
        schema="payroll",
    )
    op.drop_constraint(
        "fk_employee_tax_profile_pfa_code",
        "employee_tax_profile",
        schema="payroll",
        type_="foreignkey",
    )
    op.drop_column("employee_tax_profile", "nhf_number", schema="payroll")
    op.drop_column("employee_tax_profile", "pfa_code", schema="payroll")
    op.drop_column("employee_tax_profile", "rsa_pin", schema="payroll")
