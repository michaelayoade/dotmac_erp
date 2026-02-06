"""Add PAYE Tax tables for NTA 2025 compliance.

Revision ID: 20260123_add_paye_tax_tables
Revises: 24f0f6d22a8c
Create Date: 2026-01-23

This migration creates tables for Nigeria PAYE tax calculation under NTA 2025:
- tax_band: Progressive tax bands (0%, 15%, 18%, 21%, 23%, 25%)
- employee_tax_profile: Employee-specific tax settings (TIN, rent relief, rates)
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260123_add_paye_tax_tables"
down_revision = "24f0f6d22a8c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ========================================
    # tax_band table
    # ========================================
    op.create_table(
        "tax_band",
        sa.Column(
            "tax_band_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "name",
            sa.String(100),
            nullable=False,
            comment="Display name, e.g., 'NTA 2025 - 15%'",
        ),
        sa.Column(
            "min_amount",
            sa.Numeric(19, 4),
            nullable=False,
            comment="Lower bound (inclusive)",
        ),
        sa.Column(
            "max_amount",
            sa.Numeric(19, 4),
            nullable=True,
            comment="Upper bound (exclusive), NULL = unlimited",
        ),
        sa.Column(
            "rate",
            sa.Numeric(5, 4),
            nullable=False,
            comment="Tax rate as decimal, e.g., 0.15 for 15%",
        ),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column(
            "sequence",
            sa.Integer(),
            nullable=False,
            default=0,
            comment="Order for calculation",
        ),
        sa.Column("is_active", sa.Boolean(), default=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("tax_band_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        schema="payroll",
    )
    op.create_index(
        "idx_tax_band_org", "tax_band", ["organization_id"], schema="payroll"
    )
    op.create_index(
        "idx_tax_band_active",
        "tax_band",
        ["organization_id", "is_active", "effective_from"],
        schema="payroll",
    )
    op.create_index(
        "idx_tax_band_sequence",
        "tax_band",
        ["organization_id", "sequence"],
        schema="payroll",
    )

    # ========================================
    # employee_tax_profile table
    # ========================================
    op.create_table(
        "employee_tax_profile",
        sa.Column(
            "profile_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "tin",
            sa.String(20),
            nullable=True,
            comment="Tax Identification Number",
        ),
        sa.Column(
            "tax_state",
            sa.String(50),
            nullable=True,
            comment="State for PAYE remittance",
        ),
        sa.Column(
            "annual_rent",
            sa.Numeric(19, 4),
            nullable=True,
            default=0,
            comment="Declared annual rent for relief calculation",
        ),
        sa.Column(
            "rent_receipt_verified",
            sa.Boolean(),
            default=False,
            nullable=False,
            comment="Whether rent documentation has been verified",
        ),
        sa.Column(
            "rent_relief_amount",
            sa.Numeric(19, 4),
            nullable=True,
            comment="Calculated rent relief (20% of rent, max 500k)",
        ),
        sa.Column(
            "pension_rate",
            sa.Numeric(5, 4),
            default=0.08,
            nullable=False,
            comment="Employee pension contribution rate",
        ),
        sa.Column(
            "nhf_rate",
            sa.Numeric(5, 4),
            default=0.025,
            nullable=False,
            comment="National Housing Fund rate",
        ),
        sa.Column(
            "nhis_rate",
            sa.Numeric(5, 4),
            default=0,
            nullable=False,
            comment="National Health Insurance Scheme rate",
        ),
        sa.Column(
            "is_tax_exempt",
            sa.Boolean(),
            default=False,
            nullable=False,
            comment="Whether employee is exempt from income tax",
        ),
        sa.Column(
            "exemption_reason",
            sa.Text(),
            nullable=True,
            comment="Reason for tax exemption if applicable",
        ),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("profile_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(["employee_id"], ["hr.employee.employee_id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["people.id"]),
        sa.UniqueConstraint(
            "employee_id", "effective_from", name="uq_employee_tax_profile_emp_date"
        ),
        schema="payroll",
    )
    op.create_index(
        "idx_employee_tax_profile_org",
        "employee_tax_profile",
        ["organization_id"],
        schema="payroll",
    )
    op.create_index(
        "idx_employee_tax_profile_emp",
        "employee_tax_profile",
        ["employee_id"],
        schema="payroll",
    )
    op.create_index(
        "idx_employee_tax_profile_tin",
        "employee_tax_profile",
        ["organization_id", "tin"],
        schema="payroll",
    )

    # ========================================
    # RLS Policies for PAYE tables
    # ========================================
    paye_tables = ["tax_band", "employee_tax_profile"]

    for table in paye_tables:
        op.execute(f"ALTER TABLE payroll.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON payroll.{table}
            USING (organization_id::text = current_setting('app.current_organization_id', true))
        """
        )


def downgrade() -> None:
    # Drop RLS policies
    paye_tables = ["employee_tax_profile", "tax_band"]

    for table in paye_tables:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON payroll.{table}")
        op.execute(f"ALTER TABLE payroll.{table} DISABLE ROW LEVEL SECURITY")

    # Drop tables
    op.drop_table("employee_tax_profile", schema="payroll")
    op.drop_table("tax_band", schema="payroll")
