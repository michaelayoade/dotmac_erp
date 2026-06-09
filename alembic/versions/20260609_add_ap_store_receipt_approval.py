"""Add AP store receipt approval trigger.

Revision ID: 20260609_ap_store_receipt_approval
Revises: 20260606_learning_assessment
Create Date: 2026-06-09
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260609_ap_store_receipt_approval"
down_revision = "20260606_learning_assessment"
branch_labels = None
depends_on = None


receipt_mode_enum = postgresql.ENUM(
    "NONE",
    "AUTO_RECEIVE",
    "STORE_APPROVAL",
    name="supplier_invoice_inventory_receipt_mode",
    create_type=False,
)

approval_status_enum = postgresql.ENUM(
    "PENDING",
    "APPROVED",
    "PARTIALLY_RECEIVED",
    "REJECTED",
    "POSTED_TO_INVENTORY",
    name="invoice_inventory_receipt_approval_status",
    create_type=False,
)


def upgrade() -> None:
    op.execute("ALTER TYPE supplier_invoice_status ADD VALUE IF NOT EXISTS 'REJECTED'")
    op.execute(
        """
        DO $$
        BEGIN
            CREATE TYPE supplier_invoice_inventory_receipt_mode AS ENUM
                ('NONE', 'AUTO_RECEIVE', 'STORE_APPROVAL');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            CREATE TYPE invoice_inventory_receipt_approval_status AS ENUM
                ('PENDING', 'APPROVED', 'PARTIALLY_RECEIVED', 'REJECTED', 'POSTED_TO_INVENTORY');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    )

    op.add_column(
        "supplier_invoice",
        sa.Column(
            "inventory_receipt_mode",
            receipt_mode_enum,
            server_default="NONE",
            nullable=False,
        ),
        schema="ap",
    )
    op.execute(
        """
        UPDATE ap.supplier_invoice
        SET inventory_receipt_mode = 'AUTO_RECEIVE'
        WHERE auto_create_inventory_receipt IS TRUE
        """
    )

    op.create_table(
        "invoice_inventory_receipt_approval",
        sa.Column(
            "approval_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("supplier_invoice_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "supplier_invoice_line_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("warehouse_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requested_quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column("approved_quantity", sa.Numeric(20, 6), nullable=True),
        sa.Column("receipt_reference", sa.String(length=100), nullable=True),
        sa.Column(
            "receipt_serial_numbers", postgresql.ARRAY(sa.String()), nullable=True
        ),
        sa.Column(
            "status",
            approval_status_enum,
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column(
            "submitted_by_user_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rejected_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.String(length=500), nullable=True),
        sa.Column(
            "inventory_transaction_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["item_id"], ["inv.item.item_id"]),
        sa.ForeignKeyConstraint(["warehouse_id"], ["inv.warehouse.warehouse_id"]),
        sa.ForeignKeyConstraint(
            ["inventory_transaction_id"], ["inv.inventory_transaction.transaction_id"]
        ),
        sa.PrimaryKeyConstraint("approval_id"),
        schema="ap",
    )
    op.create_index(
        "idx_ap_inv_receipt_approval_org_status",
        "invoice_inventory_receipt_approval",
        ["organization_id", "status"],
        schema="ap",
    )
    op.create_index(
        "idx_ap_inv_receipt_approval_invoice",
        "invoice_inventory_receipt_approval",
        ["supplier_invoice_id"],
        schema="ap",
    )
    op.create_index(
        "idx_ap_inv_receipt_approval_warehouse",
        "invoice_inventory_receipt_approval",
        ["organization_id", "warehouse_id"],
        schema="ap",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_ap_inv_receipt_approval_warehouse",
        table_name="invoice_inventory_receipt_approval",
        schema="ap",
    )
    op.drop_index(
        "idx_ap_inv_receipt_approval_invoice",
        table_name="invoice_inventory_receipt_approval",
        schema="ap",
    )
    op.drop_index(
        "idx_ap_inv_receipt_approval_org_status",
        table_name="invoice_inventory_receipt_approval",
        schema="ap",
    )
    op.drop_table("invoice_inventory_receipt_approval", schema="ap")
    op.drop_column("supplier_invoice", "inventory_receipt_mode", schema="ap")

    op.execute("DROP TYPE IF EXISTS invoice_inventory_receipt_approval_status")
    op.execute("DROP TYPE IF EXISTS supplier_invoice_inventory_receipt_mode")
