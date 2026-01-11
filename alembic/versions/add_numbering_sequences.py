"""Add numbering sequence configuration.

Revision ID: add_numbering_sequences
Revises: add_quote_and_sales_order
Create Date: 2025-02-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "add_numbering_sequences"
down_revision = "add_quote_and_sales_order"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Create sequence_type enum
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'sequence_type') THEN
                CREATE TYPE sequence_type AS ENUM (
                    'INVOICE', 'CREDIT_NOTE', 'PAYMENT', 'RECEIPT', 'JOURNAL',
                    'PURCHASE_ORDER', 'SUPPLIER_INVOICE', 'ASSET', 'LEASE',
                    'GOODS_RECEIPT', 'QUOTE', 'SALES_ORDER', 'SHIPMENT', 'EXPENSE'
                );
            END IF;
        END$$;
    """)

    # Create reset_frequency enum
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'reset_frequency') THEN
                CREATE TYPE reset_frequency AS ENUM (
                    'NEVER', 'YEARLY', 'MONTHLY'
                );
            END IF;
        END$$;
    """)

    table_exists = inspector.has_table("numbering_sequence", schema="core_config")

    if not table_exists:
        # Create numbering_sequence table
        op.create_table(
            "numbering_sequence",
            sa.Column("sequence_id", UUID(as_uuid=True), primary_key=True,
                      server_default=sa.text("gen_random_uuid()")),
            sa.Column("organization_id", UUID(as_uuid=True), nullable=False),
            sa.Column(
                "sequence_type",
                postgresql.ENUM(
                    "INVOICE",
                    "CREDIT_NOTE",
                    "PAYMENT",
                    "RECEIPT",
                    "JOURNAL",
                    "PURCHASE_ORDER",
                    "SUPPLIER_INVOICE",
                    "ASSET",
                    "LEASE",
                    "GOODS_RECEIPT",
                    "QUOTE",
                    "SALES_ORDER",
                    "SHIPMENT",
                    "EXPENSE",
                    name="sequence_type",
                    create_type=False,
                ),
                nullable=False,
            ),

            # Format components
            sa.Column("prefix", sa.String(20), nullable=False, server_default=""),
            sa.Column("suffix", sa.String(10), nullable=False, server_default=""),
            sa.Column("separator", sa.String(5), nullable=False, server_default="-"),
            sa.Column("min_digits", sa.Integer, nullable=False, server_default="4"),

            # Date inclusion
            sa.Column("include_year", sa.Boolean, nullable=False, server_default="true"),
            sa.Column("include_month", sa.Boolean, nullable=False, server_default="true"),
            sa.Column("year_format", sa.Integer, nullable=False, server_default="4"),

            # Current sequence tracking
            sa.Column("current_number", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("current_year", sa.Integer, nullable=True),
            sa.Column("current_month", sa.Integer, nullable=True),

            # Reset behavior
            sa.Column(
                "reset_frequency",
                postgresql.ENUM(
                    "NEVER",
                    "YEARLY",
                    "MONTHLY",
                    name="reset_frequency",
                    create_type=False,
                ),
                nullable=False,
                server_default="MONTHLY",
            ),

            # Legacy fields
            sa.Column("fiscal_year_reset", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("fiscal_year_id", UUID(as_uuid=True), nullable=True),

            # Timestamps
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),

            # Constraints
            sa.ForeignKeyConstraint(
                ["organization_id"],
                ["core_org.organization.organization_id"],
                name="fk_numbering_org",
            ),
            sa.UniqueConstraint("organization_id", "sequence_type", name="uq_sequence_type"),
            schema="core_config",
        )

        # Create index for faster lookups
        op.create_index(
            "ix_numbering_sequence_org_type",
            "numbering_sequence",
            ["organization_id", "sequence_type"],
            schema="core_config",
        )
    else:
        existing_indexes = {
            idx["name"]
            for idx in inspector.get_indexes(
                "numbering_sequence", schema="core_config"
            )
        }
        if "ix_numbering_sequence_org_type" not in existing_indexes:
            op.create_index(
                "ix_numbering_sequence_org_type",
                "numbering_sequence",
                ["organization_id", "sequence_type"],
                schema="core_config",
            )


def downgrade() -> None:
    # Drop index
    op.drop_index(
        "ix_numbering_sequence_org_type",
        table_name="numbering_sequence",
        schema="core_config",
    )

    # Drop table
    op.drop_table("numbering_sequence", schema="core_config")

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS reset_frequency")
    op.execute("DROP TYPE IF EXISTS sequence_type")
