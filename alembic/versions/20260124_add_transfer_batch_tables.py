"""Add transfer batch tables for bulk transfers.

Revision ID: 20260124_transfer_batch
Revises: 20260124_payment_intent_fees
Create Date: 2026-01-24
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260124_transfer_batch"
down_revision = "20260124_payment_intent_fees"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enum types
    op.execute("""
        DO $$
        BEGIN
            CREATE TYPE payments.transfer_batch_status AS ENUM (
                'DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'PROCESSING',
                'COMPLETED', 'PARTIALLY_COMPLETED', 'FAILED'
            );
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$
        BEGIN
            CREATE TYPE payments.transfer_batch_item_status AS ENUM (
                'PENDING', 'PROCESSING', 'COMPLETED', 'FAILED'
            );
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
    """)

    # Create transfer_batch table
    op.create_table(
        "transfer_batch",
        sa.Column(
            "batch_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("core_org.organization.organization_id"),
            nullable=False,
        ),
        sa.Column("batch_number", sa.String(30), nullable=False),
        sa.Column("batch_date", sa.Date, nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("bank_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False, server_default="NGN"),
        sa.Column("total_transfers", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "total_amount", sa.Numeric(19, 4), nullable=False, server_default="0"
        ),
        sa.Column("total_fees", sa.Numeric(19, 4), nullable=False, server_default="0"),
        sa.Column("completed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "status",
            postgresql.ENUM(
                "DRAFT",
                "PENDING_APPROVAL",
                "APPROVED",
                "PROCESSING",
                "COMPLETED",
                "PARTIALLY_COMPLETED",
                "FAILED",
                name="transfer_batch_status",
                schema="payments",
                create_type=False,
            ),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paystack_batch_reference", sa.String(100), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "organization_id", "batch_number", name="uq_transfer_batch"
        ),
        schema="payments",
    )

    op.create_index(
        "idx_transfer_batch_org",
        "transfer_batch",
        ["organization_id"],
        schema="payments",
    )
    op.create_index(
        "idx_transfer_batch_status",
        "transfer_batch",
        ["organization_id", "status"],
        schema="payments",
    )

    # Create transfer_batch_item table
    op.create_table(
        "transfer_batch_item",
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "batch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payments.transfer_batch.batch_id"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "expense_claim_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("expense.expense_claim.claim_id"),
            nullable=False,
        ),
        sa.Column("recipient_name", sa.String(200), nullable=False),
        sa.Column("recipient_bank_code", sa.String(20), nullable=False),
        sa.Column("recipient_account_number", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(19, 4), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False, server_default="NGN"),
        sa.Column("transfer_recipient_code", sa.String(100), nullable=True),
        sa.Column("transfer_reference", sa.String(100), nullable=True),
        sa.Column("transfer_code", sa.String(100), nullable=True),
        sa.Column(
            "payment_intent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payments.payment_intent.intent_id"),
            nullable=True,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "PENDING",
                "PROCESSING",
                "COMPLETED",
                "FAILED",
                name="transfer_batch_item_status",
                schema="payments",
                create_type=False,
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("fee_amount", sa.Numeric(19, 4), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        schema="payments",
    )

    op.create_index(
        "idx_transfer_batch_item_batch",
        "transfer_batch_item",
        ["batch_id"],
        schema="payments",
    )
    op.create_index(
        "idx_transfer_batch_item_claim",
        "transfer_batch_item",
        ["expense_claim_id"],
        schema="payments",
    )
    op.create_index(
        "idx_transfer_batch_item_intent",
        "transfer_batch_item",
        ["payment_intent_id"],
        schema="payments",
    )


def downgrade() -> None:
    op.drop_table("transfer_batch_item", schema="payments")
    op.drop_table("transfer_batch", schema="payments")
    op.execute("DROP TYPE IF EXISTS payments.transfer_batch_item_status")
    op.execute("DROP TYPE IF EXISTS payments.transfer_batch_status")
