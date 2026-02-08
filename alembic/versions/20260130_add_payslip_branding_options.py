"""Add payslip customization fields to organization_branding.

Supports different payslip templates and display options per organization.

Revision ID: 20260130_add_payslip_branding_options
Revises: 20260130_add_email_profiles
Create Date: 2026-01-30
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260130_add_payslip_branding_options"
down_revision = "20260130_add_email_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add payslip customization columns to organization_branding
    op.add_column(
        "organization_branding",
        sa.Column(
            "payslip_template",
            sa.String(50),
            nullable=True,
            server_default="default",
            comment="Payslip template variant: default, compact, detailed",
        ),
        schema="core_org",
    )
    op.add_column(
        "organization_branding",
        sa.Column(
            "payslip_footer_text",
            sa.Text(),
            nullable=True,
            comment="Custom footer text on payslips",
        ),
        schema="core_org",
    )
    op.add_column(
        "organization_branding",
        sa.Column(
            "payslip_show_ytd",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
            comment="Show year-to-date totals on payslips",
        ),
        schema="core_org",
    )
    op.add_column(
        "organization_branding",
        sa.Column(
            "payslip_show_tax_breakdown",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
            comment="Show detailed tax band breakdown on payslips",
        ),
        schema="core_org",
    )
    op.add_column(
        "organization_branding",
        sa.Column(
            "payslip_show_bank_details",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
            comment="Show bank account details on payslips",
        ),
        schema="core_org",
    )
    op.add_column(
        "organization_branding",
        sa.Column(
            "payslip_confidentiality_notice",
            sa.Text(),
            nullable=True,
            comment="Confidentiality notice printed on payslips",
        ),
        schema="core_org",
    )

    # Add CHECK constraint for valid payslip templates
    op.create_check_constraint(
        "ck_organization_branding_payslip_template",
        "organization_branding",
        "payslip_template IS NULL OR payslip_template IN ('default', 'compact', 'detailed')",
        schema="core_org",
    )


def downgrade() -> None:
    # Drop CHECK constraint first
    op.execute(
        "ALTER TABLE core_org.organization_branding "
        "DROP CONSTRAINT IF EXISTS ck_organization_branding_payslip_template"
    )
    # Use raw SQL with IF EXISTS for idempotent downgrades
    columns_to_drop = [
        "payslip_confidentiality_notice",
        "payslip_show_bank_details",
        "payslip_show_tax_breakdown",
        "payslip_show_ytd",
        "payslip_footer_text",
        "payslip_template",
    ]
    for col in columns_to_drop:
        op.execute(
            f"ALTER TABLE core_org.organization_branding DROP COLUMN IF EXISTS {col}"
        )
