"""Add banking categorization tables (payee, transaction_rule).

Revision ID: add_banking_categorization
Revises: add_numbering_sequences
Create Date: 2025-02-04
"""

from alembic import op
from app.alembic_utils import ensure_enum
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID, JSONB

# revision identifiers, used by Alembic.
revision = "add_banking_categorization"
down_revision = "add_numbering_sequences"
branch_labels = None
depends_on = "add_banking_schema"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Create payee_type enum
    ensure_enum(
        bind,
        "payee_type",
        "VENDOR",
        "CUSTOMER",
        "EMPLOYEE",
        "BANK",
        "TAX",
        "UTILITY",
        "OTHER",
        schema="banking",
    )

    # Create rule_type enum
    ensure_enum(
        bind,
        "rule_type",
        "PAYEE_MATCH",
        "DESCRIPTION_CONTAINS",
        "DESCRIPTION_REGEX",
        "AMOUNT_RANGE",
        "REFERENCE_MATCH",
        "COMBINED",
        schema="banking",
    )

    # Create rule_action enum
    ensure_enum(
        bind,
        "rule_action",
        "CATEGORIZE",
        "FLAG_REVIEW",
        "SPLIT",
        "IGNORE",
        schema="banking",
    )

    # Create payee table
    if not inspector.has_table("payee", schema="banking"):
        op.create_table(
            "payee",
            sa.Column(
                "payee_id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("organization_id", UUID(as_uuid=True), nullable=False),
            sa.Column("payee_name", sa.String(200), nullable=False),
            sa.Column(
                "payee_type",
                postgresql.ENUM(
                    "VENDOR",
                    "CUSTOMER",
                    "EMPLOYEE",
                    "BANK",
                    "TAX",
                    "UTILITY",
                    "OTHER",
                    name="payee_type",
                    create_type=False,
                ),
                nullable=False,
                server_default="OTHER",
            ),
            # Matching patterns (pipe-separated)
            sa.Column("name_patterns", sa.Text, nullable=True),
            # Default categorization
            sa.Column("default_account_id", UUID(as_uuid=True), nullable=True),
            sa.Column("default_tax_code_id", UUID(as_uuid=True), nullable=True),
            # Link to master records
            sa.Column("supplier_id", UUID(as_uuid=True), nullable=True),
            sa.Column("customer_id", UUID(as_uuid=True), nullable=True),
            # Usage tracking
            sa.Column("match_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("last_matched_at", sa.DateTime(timezone=True), nullable=True),
            # Status and metadata
            sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("created_by", UUID(as_uuid=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("notes", sa.Text, nullable=True),
            # Foreign keys
            sa.ForeignKeyConstraint(
                ["organization_id"],
                ["core_org.organization.organization_id"],
                name="fk_payee_org",
            ),
            sa.ForeignKeyConstraint(
                ["default_account_id"],
                ["gl.account.account_id"],
                name="fk_payee_default_account",
            ),
            sa.ForeignKeyConstraint(
                ["supplier_id"],
                ["ap.supplier.supplier_id"],
                name="fk_payee_supplier",
            ),
            sa.ForeignKeyConstraint(
                ["customer_id"],
                ["ar.customer.customer_id"],
                name="fk_payee_customer",
            ),
            # Unique constraint
            sa.UniqueConstraint("organization_id", "payee_name", name="uq_payee_name"),
            schema="banking",
        )

        # Create indexes
        op.create_index(
            "ix_payee_org",
            "payee",
            ["organization_id"],
            schema="banking",
        )
        op.create_index(
            "ix_payee_type",
            "payee",
            ["payee_type"],
            schema="banking",
        )

    # Create transaction_rule table
    if not inspector.has_table("transaction_rule", schema="banking"):
        op.create_table(
            "transaction_rule",
            sa.Column(
                "rule_id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("organization_id", UUID(as_uuid=True), nullable=False),
            sa.Column("rule_name", sa.String(200), nullable=False),
            sa.Column("description", sa.Text, nullable=True),
            # Rule type and conditions
            sa.Column(
                "rule_type",
                postgresql.ENUM(
                    "PAYEE_MATCH",
                    "DESCRIPTION_CONTAINS",
                    "DESCRIPTION_REGEX",
                    "AMOUNT_RANGE",
                    "REFERENCE_MATCH",
                    "COMBINED",
                    name="rule_type",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column("conditions", JSONB, nullable=False, server_default="{}"),
            # Scope filters
            sa.Column("bank_account_id", UUID(as_uuid=True), nullable=True),
            sa.Column(
                "applies_to_credits", sa.Boolean, nullable=False, server_default="true"
            ),
            sa.Column(
                "applies_to_debits", sa.Boolean, nullable=False, server_default="true"
            ),
            # Action configuration
            sa.Column(
                "action",
                postgresql.ENUM(
                    "CATEGORIZE",
                    "FLAG_REVIEW",
                    "SPLIT",
                    "IGNORE",
                    name="rule_action",
                    create_type=False,
                ),
                nullable=False,
                server_default="CATEGORIZE",
            ),
            sa.Column("target_account_id", UUID(as_uuid=True), nullable=True),
            sa.Column("tax_code_id", UUID(as_uuid=True), nullable=True),
            sa.Column("split_config", JSONB, nullable=True),
            # Execution settings
            sa.Column("priority", sa.Integer, nullable=False, server_default="100"),
            sa.Column("auto_apply", sa.Boolean, nullable=False, server_default="false"),
            sa.Column(
                "min_confidence", sa.Integer, nullable=False, server_default="80"
            ),
            # Usage tracking
            sa.Column("match_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("success_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("reject_count", sa.Integer, nullable=False, server_default="0"),
            # Status and metadata
            sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("created_by", UUID(as_uuid=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            # Foreign keys
            sa.ForeignKeyConstraint(
                ["organization_id"],
                ["core_org.organization.organization_id"],
                name="fk_rule_org",
            ),
            sa.ForeignKeyConstraint(
                ["bank_account_id"],
                ["banking.bank_accounts.bank_account_id"],
                name="fk_rule_bank_account",
            ),
            sa.ForeignKeyConstraint(
                ["target_account_id"],
                ["gl.account.account_id"],
                name="fk_rule_target_account",
            ),
            # Unique constraint
            sa.UniqueConstraint("organization_id", "rule_name", name="uq_rule_name"),
            schema="banking",
        )

        # Create indexes
        op.create_index(
            "ix_rule_org",
            "transaction_rule",
            ["organization_id"],
            schema="banking",
        )
        op.create_index(
            "ix_rule_priority",
            "transaction_rule",
            ["priority"],
            schema="banking",
        )
        op.create_index(
            "ix_rule_type",
            "transaction_rule",
            ["rule_type"],
            schema="banking",
        )


def downgrade() -> None:
    # Drop indexes
    op.drop_index("ix_rule_type", table_name="transaction_rule", schema="banking")
    op.drop_index("ix_rule_priority", table_name="transaction_rule", schema="banking")
    op.drop_index("ix_rule_org", table_name="transaction_rule", schema="banking")
    op.drop_index("ix_payee_type", table_name="payee", schema="banking")
    op.drop_index("ix_payee_org", table_name="payee", schema="banking")

    # Drop tables
    op.drop_table("transaction_rule", schema="banking")
    op.drop_table("payee", schema="banking")

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS rule_action")
    op.execute("DROP TYPE IF EXISTS rule_type")
    op.execute("DROP TYPE IF EXISTS payee_type")
