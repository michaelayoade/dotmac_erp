"""Add Remita RRR table for government payments

Revision ID: 20260201_add_remita_rrr_table
Revises: 20260201_merge_heads_for_remita
Create Date: 2026-02-01

This migration creates the remita_rrr table in the payments schema for
tracking Remita Retrieval References (RRRs) used for government payments
like PAYE, NHF, Pension, taxes, and fees.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260201_add_remita_rrr_table"
down_revision = "20260201_merge_heads_for_remita"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Create RRR status enum if not exists
    existing_enums = [e["name"] for e in inspector.get_enums(schema="payments")]
    if "rrr_status" not in existing_enums:
        rrr_status_enum = postgresql.ENUM(
            "pending",
            "paid",
            "expired",
            "failed",
            "cancelled",
            name="rrr_status",
            schema="payments",
            create_type=False,
        )
        rrr_status_enum.create(bind)

    # Create table if not exists
    if not inspector.has_table("remita_rrr", schema="payments"):
        # Reference the enum (may have been created above or previously)
        rrr_status_enum = postgresql.ENUM(
            "pending",
            "paid",
            "expired",
            "failed",
            "cancelled",
            name="rrr_status",
            schema="payments",
            create_type=False,
        )

        op.create_table(
            "remita_rrr",
            # Primary key
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            # Organization (multi-tenant)
            sa.Column(
                "organization_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
                index=True,
            ),
            # The RRR itself
            sa.Column("rrr", sa.String(20), nullable=False, unique=True, index=True),
            sa.Column("order_id", sa.String(100), nullable=False, index=True),
            sa.Column("amount", sa.Numeric(18, 2), nullable=False),
            # Payer information
            sa.Column("payer_name", sa.String(200), nullable=False),
            sa.Column("payer_email", sa.String(255), nullable=False),
            sa.Column("payer_phone", sa.String(20), nullable=True),
            # Biller/service details
            sa.Column("biller_id", sa.String(50), nullable=False, index=True),
            sa.Column("biller_name", sa.String(200), nullable=False),
            sa.Column("service_type_id", sa.String(50), nullable=False),
            sa.Column("service_name", sa.String(200), nullable=False),
            sa.Column("description", sa.Text, nullable=False),
            # Source linking
            sa.Column("source_type", sa.String(50), nullable=True),
            sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
            # Status
            sa.Column(
                "status",
                rrr_status_enum,
                nullable=False,
                server_default="pending",
            ),
            # Timestamps
            sa.Column(
                "generated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
            # Payment info
            sa.Column("payment_reference", sa.String(100), nullable=True),
            sa.Column("payment_channel", sa.String(50), nullable=True),
            # API response storage
            sa.Column("api_response", postgresql.JSONB, nullable=True),
            sa.Column("last_status_check", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_status_response", postgresql.JSONB, nullable=True),
            # Audit fields
            sa.Column(
                "created_by_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("people.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            schema="payments",
        )

    # Create composite indexes if table exists and indexes don't
    if inspector.has_table("remita_rrr", schema="payments"):
        existing_indexes = {
            idx["name"]
            for idx in inspector.get_indexes("remita_rrr", schema="payments")
        }

        if "ix_remita_rrr_org_status" not in existing_indexes:
            op.create_index(
                "ix_remita_rrr_org_status",
                "remita_rrr",
                ["organization_id", "status"],
                schema="payments",
            )

        if "ix_remita_rrr_source" not in existing_indexes:
            op.create_index(
                "ix_remita_rrr_source",
                "remita_rrr",
                ["source_type", "source_id"],
                schema="payments",
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("remita_rrr", schema="payments"):
        # Drop indexes first (check if they exist)
        existing_indexes = {
            idx["name"]
            for idx in inspector.get_indexes("remita_rrr", schema="payments")
        }

        if "ix_remita_rrr_source" in existing_indexes:
            op.drop_index(
                "ix_remita_rrr_source", table_name="remita_rrr", schema="payments"
            )

        if "ix_remita_rrr_org_status" in existing_indexes:
            op.drop_index(
                "ix_remita_rrr_org_status", table_name="remita_rrr", schema="payments"
            )

        # Drop table
        op.drop_table("remita_rrr", schema="payments")

    # Drop enum type if it exists
    existing_enums = [e["name"] for e in inspector.get_enums(schema="payments")]
    if "rrr_status" in existing_enums:
        rrr_status_enum = postgresql.ENUM(name="rrr_status", schema="payments")
        rrr_status_enum.drop(bind)
