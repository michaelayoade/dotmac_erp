"""Create IPSAS fund accounting schema and tables.

Revision ID: 20260203_create_ipsas_schema
Revises: 20260203_create_procurement_schema
Create Date: 2026-02-03

This migration creates:
- ipsas schema for IPSAS fund accounting
- Enums: fund_type, fund_status, appropriation_type, appropriation_status,
         allotment_status, commitment_type, commitment_status, virement_status,
         coa_segment_type
- Tables: fund, appropriation, allotment, commitment, commitment_line,
          virement, coa_segment_definition, coa_segment_value
- Organization columns: sector_type, accounting_framework,
                        fund_accounting_enabled, commitment_control_enabled
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.alembic_utils import ensure_enum

revision = "20260203_create_ipsas_schema"
down_revision = "20260203_create_procurement_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ========================================
    # Create ipsas schema
    # ========================================
    op.execute("CREATE SCHEMA IF NOT EXISTS ipsas")

    # ========================================
    # Create enum types
    # ========================================
    ensure_enum(
        bind,
        "fund_type",
        "GENERAL",
        "CAPITAL",
        "SPECIAL",
        "DONOR",
        "TRUST",
        "REVOLVING",
        "CONSOLIDATED",
        schema="ipsas",
    )
    ensure_enum(
        bind,
        "fund_status",
        "ACTIVE",
        "FROZEN",
        "CLOSED",
        schema="ipsas",
    )
    ensure_enum(
        bind,
        "appropriation_type",
        "ORIGINAL",
        "SUPPLEMENTARY",
        "VIREMENT_IN",
        "VIREMENT_OUT",
        "REDUCTION",
        schema="ipsas",
    )
    ensure_enum(
        bind,
        "appropriation_status",
        "DRAFT",
        "SUBMITTED",
        "APPROVED",
        "ACTIVE",
        "LAPSED",
        "CLOSED",
        schema="ipsas",
    )
    ensure_enum(
        bind,
        "allotment_status",
        "ACTIVE",
        "FROZEN",
        "CLOSED",
        schema="ipsas",
    )
    ensure_enum(
        bind,
        "commitment_type",
        "PURCHASE_ORDER",
        "CONTRACT",
        "PAYROLL",
        "OTHER",
        schema="ipsas",
    )
    ensure_enum(
        bind,
        "commitment_status",
        "PENDING",
        "COMMITTED",
        "OBLIGATED",
        "PARTIALLY_PAID",
        "EXPENDED",
        "CANCELLED",
        "LAPSED",
        schema="ipsas",
    )
    ensure_enum(
        bind,
        "virement_status",
        "DRAFT",
        "SUBMITTED",
        "APPROVED",
        "APPLIED",
        "REJECTED",
        schema="ipsas",
    )
    ensure_enum(
        bind,
        "coa_segment_type",
        "ADMINISTRATIVE",
        "ECONOMIC",
        "FUND",
        "FUNCTIONAL",
        "PROGRAM",
        "PROJECT",
        schema="ipsas",
    )

    # ========================================
    # Organization enums (core_org schema)
    # ========================================
    existing_enums = [e["name"] for e in inspector.get_enums(schema="core_org")]

    if "sector_type" not in existing_enums:
        ensure_enum(
            bind,
            "sector_type",
            "PRIVATE",
            "PUBLIC",
            "NGO",
            schema="core_org",
        )
    if "accounting_framework" not in existing_enums:
        ensure_enum(
            bind,
            "accounting_framework",
            "IFRS",
            "IPSAS",
            "BOTH",
            schema="core_org",
        )

    # ========================================
    # Add columns to core_org.organization
    # ========================================
    if inspector.has_table("organization", schema="core_org"):
        columns = {
            col["name"]
            for col in inspector.get_columns("organization", schema="core_org")
        }
        if "sector_type" not in columns:
            op.add_column(
                "organization",
                sa.Column(
                    "sector_type",
                    postgresql.ENUM(
                        "PRIVATE",
                        "PUBLIC",
                        "NGO",
                        name="sector_type",
                        schema="core_org",
                        create_type=False,
                    ),
                    nullable=False,
                    server_default="PRIVATE",
                ),
                schema="core_org",
            )
        if "accounting_framework" not in columns:
            op.add_column(
                "organization",
                sa.Column(
                    "accounting_framework",
                    postgresql.ENUM(
                        "IFRS",
                        "IPSAS",
                        "BOTH",
                        name="accounting_framework",
                        schema="core_org",
                        create_type=False,
                    ),
                    nullable=False,
                    server_default="IFRS",
                ),
                schema="core_org",
            )
        if "fund_accounting_enabled" not in columns:
            op.add_column(
                "organization",
                sa.Column(
                    "fund_accounting_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                ),
                schema="core_org",
            )
        if "commitment_control_enabled" not in columns:
            op.add_column(
                "organization",
                sa.Column(
                    "commitment_control_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                ),
                schema="core_org",
            )

    # ========================================
    # fund table
    # ========================================
    if not inspector.has_table("fund", schema="ipsas"):
        op.create_table(
            "fund",
            sa.Column(
                "fund_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("fund_code", sa.String(20), nullable=False),
            sa.Column("fund_name", sa.String(200), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column(
                "fund_type",
                postgresql.ENUM(
                    "GENERAL",
                    "CAPITAL",
                    "SPECIAL",
                    "DONOR",
                    "TRUST",
                    "REVOLVING",
                    "CONSOLIDATED",
                    name="fund_type",
                    schema="ipsas",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "ACTIVE",
                    "FROZEN",
                    "CLOSED",
                    name="fund_status",
                    schema="ipsas",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column(
                "is_restricted", sa.Boolean(), nullable=False, server_default="false"
            ),
            sa.Column("restriction_description", sa.Text(), nullable=True),
            sa.Column("donor_name", sa.String(200), nullable=True),
            sa.Column("donor_reference", sa.String(100), nullable=True),
            sa.Column("effective_from", sa.Date(), nullable=False),
            sa.Column("effective_to", sa.Date(), nullable=True),
            sa.Column("parent_fund_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column(
                "created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("fund_id"),
            sa.ForeignKeyConstraint(
                ["organization_id"], ["core_org.organization.organization_id"]
            ),
            sa.ForeignKeyConstraint(["parent_fund_id"], ["ipsas.fund.fund_id"]),
            sa.UniqueConstraint("organization_id", "fund_code", name="uq_fund_code"),
            schema="ipsas",
        )
        op.create_index(
            "idx_fund_org_status",
            "fund",
            ["organization_id", "status"],
            schema="ipsas",
        )
        op.create_index(
            "idx_fund_type",
            "fund",
            ["organization_id", "fund_type"],
            schema="ipsas",
        )

    # ========================================
    # appropriation table
    # ========================================
    if not inspector.has_table("appropriation", schema="ipsas"):
        op.create_table(
            "appropriation",
            sa.Column(
                "appropriation_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("fiscal_year_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("fund_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("budget_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("appropriation_code", sa.String(30), nullable=False),
            sa.Column("appropriation_name", sa.String(200), nullable=False),
            sa.Column(
                "appropriation_type",
                postgresql.ENUM(
                    "ORIGINAL",
                    "SUPPLEMENTARY",
                    "VIREMENT_IN",
                    "VIREMENT_OUT",
                    "REDUCTION",
                    name="appropriation_type",
                    schema="ipsas",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "DRAFT",
                    "SUBMITTED",
                    "APPROVED",
                    "ACTIVE",
                    "LAPSED",
                    "CLOSED",
                    name="appropriation_status",
                    schema="ipsas",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column(
                "approved_amount", sa.Numeric(20, 6), nullable=False, server_default="0"
            ),
            sa.Column(
                "revised_amount", sa.Numeric(20, 6), nullable=False, server_default="0"
            ),
            sa.Column("currency_code", sa.String(3), nullable=False),
            sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("cost_center_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("business_unit_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("appropriation_act_reference", sa.String(100), nullable=True),
            sa.Column("effective_from", sa.Date(), nullable=False),
            sa.Column("effective_to", sa.Date(), nullable=True),
            sa.Column(
                "created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False
            ),
            sa.Column(
                "approved_by_user_id", postgresql.UUID(as_uuid=True), nullable=True
            ),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("appropriation_id"),
            sa.ForeignKeyConstraint(
                ["organization_id"], ["core_org.organization.organization_id"]
            ),
            sa.ForeignKeyConstraint(
                ["fiscal_year_id"], ["gl.fiscal_year.fiscal_year_id"]
            ),
            sa.ForeignKeyConstraint(["fund_id"], ["ipsas.fund.fund_id"]),
            sa.ForeignKeyConstraint(["budget_id"], ["gl.budget.budget_id"]),
            sa.ForeignKeyConstraint(["account_id"], ["gl.account.account_id"]),
            sa.UniqueConstraint(
                "organization_id", "appropriation_code", name="uq_appropriation_code"
            ),
            schema="ipsas",
        )
        op.create_index(
            "idx_approp_org_fy",
            "appropriation",
            ["organization_id", "fiscal_year_id"],
            schema="ipsas",
        )
        op.create_index(
            "idx_approp_fund",
            "appropriation",
            ["fund_id"],
            schema="ipsas",
        )

    # ========================================
    # allotment table
    # ========================================
    if not inspector.has_table("allotment", schema="ipsas"):
        op.create_table(
            "allotment",
            sa.Column(
                "allotment_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "appropriation_id", postgresql.UUID(as_uuid=True), nullable=False
            ),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("allotment_code", sa.String(30), nullable=False),
            sa.Column("allotment_name", sa.String(200), nullable=False),
            sa.Column("cost_center_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("business_unit_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column(
                "allotted_amount", sa.Numeric(20, 6), nullable=False, server_default="0"
            ),
            sa.Column("period_from", sa.Date(), nullable=False),
            sa.Column("period_to", sa.Date(), nullable=False),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "ACTIVE",
                    "FROZEN",
                    "CLOSED",
                    name="allotment_status",
                    schema="ipsas",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("allotment_id"),
            sa.ForeignKeyConstraint(
                ["appropriation_id"], ["ipsas.appropriation.appropriation_id"]
            ),
            sa.ForeignKeyConstraint(
                ["organization_id"], ["core_org.organization.organization_id"]
            ),
            sa.UniqueConstraint(
                "appropriation_id", "allotment_code", name="uq_allotment_code"
            ),
            schema="ipsas",
        )
        op.create_index(
            "idx_allotment_approp",
            "allotment",
            ["appropriation_id"],
            schema="ipsas",
        )

    # ========================================
    # commitment table
    # ========================================
    if not inspector.has_table("commitment", schema="ipsas"):
        op.create_table(
            "commitment",
            sa.Column(
                "commitment_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("commitment_number", sa.String(30), nullable=False),
            sa.Column(
                "commitment_type",
                postgresql.ENUM(
                    "PURCHASE_ORDER",
                    "CONTRACT",
                    "PAYROLL",
                    "OTHER",
                    name="commitment_type",
                    schema="ipsas",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "PENDING",
                    "COMMITTED",
                    "OBLIGATED",
                    "PARTIALLY_PAID",
                    "EXPENDED",
                    "CANCELLED",
                    "LAPSED",
                    name="commitment_status",
                    schema="ipsas",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column("appropriation_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("allotment_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("fund_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "source_type",
                sa.String(50),
                nullable=False,
                comment="purchase_order, contract, payroll, etc.",
            ),
            sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("cost_center_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("business_unit_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("fiscal_year_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "fiscal_period_id", postgresql.UUID(as_uuid=True), nullable=False
            ),
            sa.Column("currency_code", sa.String(3), nullable=False),
            sa.Column(
                "committed_amount",
                sa.Numeric(20, 6),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "obligated_amount",
                sa.Numeric(20, 6),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "expended_amount", sa.Numeric(20, 6), nullable=False, server_default="0"
            ),
            sa.Column(
                "cancelled_amount",
                sa.Numeric(20, 6),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "commitment_journal_id",
                postgresql.UUID(as_uuid=True),
                nullable=True,
                comment="Encumbrance journal entry",
            ),
            sa.Column(
                "obligation_journal_id",
                postgresql.UUID(as_uuid=True),
                nullable=True,
                comment="Obligation journal entry",
            ),
            sa.Column("commitment_date", sa.Date(), nullable=False),
            sa.Column("obligation_date", sa.Date(), nullable=True),
            sa.Column("expenditure_date", sa.Date(), nullable=True),
            sa.Column(
                "created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("commitment_id"),
            sa.ForeignKeyConstraint(
                ["organization_id"], ["core_org.organization.organization_id"]
            ),
            sa.ForeignKeyConstraint(
                ["appropriation_id"], ["ipsas.appropriation.appropriation_id"]
            ),
            sa.ForeignKeyConstraint(["allotment_id"], ["ipsas.allotment.allotment_id"]),
            sa.ForeignKeyConstraint(["fund_id"], ["ipsas.fund.fund_id"]),
            sa.ForeignKeyConstraint(["account_id"], ["gl.account.account_id"]),
            sa.ForeignKeyConstraint(
                ["fiscal_year_id"], ["gl.fiscal_year.fiscal_year_id"]
            ),
            sa.ForeignKeyConstraint(
                ["fiscal_period_id"], ["gl.fiscal_period.fiscal_period_id"]
            ),
            sa.UniqueConstraint(
                "organization_id", "commitment_number", name="uq_commitment_number"
            ),
            schema="ipsas",
        )
        op.create_index(
            "idx_commitment_org_status",
            "commitment",
            ["organization_id", "status"],
            schema="ipsas",
        )
        op.create_index(
            "idx_commitment_fund",
            "commitment",
            ["fund_id"],
            schema="ipsas",
        )
        op.create_index(
            "idx_commitment_approp",
            "commitment",
            ["appropriation_id"],
            schema="ipsas",
        )
        op.create_index(
            "idx_commitment_source",
            "commitment",
            ["source_type", "source_id"],
            schema="ipsas",
        )

    # ========================================
    # commitment_line table
    # ========================================
    if not inspector.has_table("commitment_line", schema="ipsas"):
        op.create_table(
            "commitment_line",
            sa.Column(
                "line_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("commitment_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("line_number", sa.Integer(), nullable=False),
            sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column(
                "committed_amount",
                sa.Numeric(20, 6),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "obligated_amount",
                sa.Numeric(20, 6),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "expended_amount", sa.Numeric(20, 6), nullable=False, server_default="0"
            ),
            sa.Column("source_line_type", sa.String(50), nullable=True),
            sa.Column("source_line_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.PrimaryKeyConstraint("line_id"),
            sa.ForeignKeyConstraint(
                ["commitment_id"], ["ipsas.commitment.commitment_id"]
            ),
            sa.ForeignKeyConstraint(["account_id"], ["gl.account.account_id"]),
            schema="ipsas",
        )
        op.create_index(
            "idx_commitment_line_commitment",
            "commitment_line",
            ["commitment_id"],
            schema="ipsas",
        )

    # ========================================
    # virement table
    # ========================================
    if not inspector.has_table("virement", schema="ipsas"):
        op.create_table(
            "virement",
            sa.Column(
                "virement_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("fiscal_year_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("virement_number", sa.String(30), nullable=False),
            sa.Column("description", sa.String(500), nullable=False),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "DRAFT",
                    "SUBMITTED",
                    "APPROVED",
                    "APPLIED",
                    "REJECTED",
                    name="virement_status",
                    schema="ipsas",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column(
                "from_appropriation_id", postgresql.UUID(as_uuid=True), nullable=False
            ),
            sa.Column("from_account_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column(
                "from_cost_center_id", postgresql.UUID(as_uuid=True), nullable=True
            ),
            sa.Column("from_fund_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column(
                "to_appropriation_id", postgresql.UUID(as_uuid=True), nullable=False
            ),
            sa.Column("to_account_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column(
                "to_cost_center_id", postgresql.UUID(as_uuid=True), nullable=True
            ),
            sa.Column("to_fund_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("amount", sa.Numeric(20, 6), nullable=False),
            sa.Column("currency_code", sa.String(3), nullable=False),
            sa.Column("justification", sa.Text(), nullable=False),
            sa.Column("approval_authority", sa.String(100), nullable=True),
            sa.Column(
                "created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False
            ),
            sa.Column(
                "approved_by_user_id", postgresql.UUID(as_uuid=True), nullable=True
            ),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("virement_id"),
            sa.ForeignKeyConstraint(
                ["organization_id"], ["core_org.organization.organization_id"]
            ),
            sa.ForeignKeyConstraint(
                ["fiscal_year_id"], ["gl.fiscal_year.fiscal_year_id"]
            ),
            sa.ForeignKeyConstraint(
                ["from_appropriation_id"], ["ipsas.appropriation.appropriation_id"]
            ),
            sa.ForeignKeyConstraint(
                ["to_appropriation_id"], ["ipsas.appropriation.appropriation_id"]
            ),
            sa.UniqueConstraint(
                "organization_id", "virement_number", name="uq_virement_number"
            ),
            schema="ipsas",
        )
        op.create_index(
            "idx_virement_org_status",
            "virement",
            ["organization_id", "status"],
            schema="ipsas",
        )
        op.create_index(
            "idx_virement_fy",
            "virement",
            ["fiscal_year_id"],
            schema="ipsas",
        )

    # ========================================
    # coa_segment_definition table
    # ========================================
    if not inspector.has_table("coa_segment_definition", schema="ipsas"):
        op.create_table(
            "coa_segment_definition",
            sa.Column(
                "segment_def_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "segment_type",
                postgresql.ENUM(
                    "ADMINISTRATIVE",
                    "ECONOMIC",
                    "FUND",
                    "FUNCTIONAL",
                    "PROGRAM",
                    "PROJECT",
                    name="coa_segment_type",
                    schema="ipsas",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column("segment_name", sa.String(100), nullable=False),
            sa.Column("code_position_start", sa.Integer(), nullable=False),
            sa.Column("code_length", sa.Integer(), nullable=False),
            sa.Column("separator", sa.String(1), nullable=False, server_default="-"),
            sa.Column(
                "is_required", sa.Boolean(), nullable=False, server_default="true"
            ),
            sa.Column("display_order", sa.Integer(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.PrimaryKeyConstraint("segment_def_id"),
            sa.ForeignKeyConstraint(
                ["organization_id"], ["core_org.organization.organization_id"]
            ),
            sa.UniqueConstraint(
                "organization_id", "segment_type", name="uq_coa_segment_def"
            ),
            schema="ipsas",
        )
        op.create_index(
            "idx_coa_seg_def_org",
            "coa_segment_definition",
            ["organization_id"],
            schema="ipsas",
        )

    # ========================================
    # coa_segment_value table
    # ========================================
    if not inspector.has_table("coa_segment_value", schema="ipsas"):
        op.create_table(
            "coa_segment_value",
            sa.Column(
                "segment_value_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("segment_def_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("segment_code", sa.String(20), nullable=False),
            sa.Column("segment_name", sa.String(200), nullable=False),
            sa.Column(
                "parent_segment_value_id",
                postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.PrimaryKeyConstraint("segment_value_id"),
            sa.ForeignKeyConstraint(
                ["segment_def_id"], ["ipsas.coa_segment_definition.segment_def_id"]
            ),
            sa.ForeignKeyConstraint(
                ["organization_id"], ["core_org.organization.organization_id"]
            ),
            sa.ForeignKeyConstraint(
                ["parent_segment_value_id"],
                ["ipsas.coa_segment_value.segment_value_id"],
            ),
            sa.UniqueConstraint(
                "segment_def_id", "segment_code", name="uq_coa_segment_value"
            ),
            schema="ipsas",
        )
        op.create_index(
            "idx_coa_seg_val_def",
            "coa_segment_value",
            ["segment_def_id"],
            schema="ipsas",
        )
        op.create_index(
            "idx_coa_seg_val_org",
            "coa_segment_value",
            ["organization_id"],
            schema="ipsas",
        )

    # ========================================
    # RLS Policies
    # ========================================
    ipsas_tables = [
        "fund",
        "appropriation",
        "allotment",
        "commitment",
        "virement",
        "coa_segment_definition",
        "coa_segment_value",
    ]
    for table in ipsas_tables:
        op.execute(f"ALTER TABLE ipsas.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON ipsas.{table}
            USING (organization_id::text = current_setting('app.current_organization_id', true))
            """
        )

    # commitment_line has no organization_id; enforce tenant isolation via commitment join
    op.execute("ALTER TABLE ipsas.commitment_line ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY commitment_line_tenant_isolation ON ipsas.commitment_line
        USING (
            EXISTS (
                SELECT 1
                FROM ipsas.commitment c
                WHERE c.commitment_id = commitment_line.commitment_id
                AND c.organization_id::text = current_setting('app.current_organization_id', true)
            )
        )
        """
    )


def downgrade() -> None:
    # Drop RLS policies
    ipsas_tables = [
        "coa_segment_value",
        "coa_segment_definition",
        "virement",
        "commitment_line",
        "commitment",
        "allotment",
        "appropriation",
        "fund",
    ]
    for table in ipsas_tables:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON ipsas.{table}")
        op.execute(f"ALTER TABLE ipsas.{table} DISABLE ROW LEVEL SECURITY")

    op.execute(
        "DROP POLICY IF EXISTS commitment_line_tenant_isolation ON ipsas.commitment_line"
    )

    # Drop tables in reverse dependency order
    op.drop_table("coa_segment_value", schema="ipsas")
    op.drop_table("coa_segment_definition", schema="ipsas")
    op.drop_table("virement", schema="ipsas")
    op.drop_table("commitment_line", schema="ipsas")
    op.drop_table("commitment", schema="ipsas")
    op.drop_table("allotment", schema="ipsas")
    op.drop_table("appropriation", schema="ipsas")
    op.drop_table("fund", schema="ipsas")

    # Drop ipsas enums
    op.execute("DROP TYPE IF EXISTS ipsas.coa_segment_type")
    op.execute("DROP TYPE IF EXISTS ipsas.virement_status")
    op.execute("DROP TYPE IF EXISTS ipsas.commitment_status")
    op.execute("DROP TYPE IF EXISTS ipsas.commitment_type")
    op.execute("DROP TYPE IF EXISTS ipsas.allotment_status")
    op.execute("DROP TYPE IF EXISTS ipsas.appropriation_status")
    op.execute("DROP TYPE IF EXISTS ipsas.appropriation_type")
    op.execute("DROP TYPE IF EXISTS ipsas.fund_status")
    op.execute("DROP TYPE IF EXISTS ipsas.fund_type")

    # Drop ipsas schema
    op.execute("DROP SCHEMA IF EXISTS ipsas")

    # Remove org columns
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("organization", schema="core_org"):
        columns = {
            col["name"]
            for col in inspector.get_columns("organization", schema="core_org")
        }
        if "commitment_control_enabled" in columns:
            op.drop_column(
                "organization", "commitment_control_enabled", schema="core_org"
            )
        if "fund_accounting_enabled" in columns:
            op.drop_column("organization", "fund_accounting_enabled", schema="core_org")
        if "accounting_framework" in columns:
            op.drop_column("organization", "accounting_framework", schema="core_org")
        if "sector_type" in columns:
            op.drop_column("organization", "sector_type", schema="core_org")

    # Drop core_org enums
    op.execute("DROP TYPE IF EXISTS core_org.accounting_framework")
    op.execute("DROP TYPE IF EXISTS core_org.sector_type")
