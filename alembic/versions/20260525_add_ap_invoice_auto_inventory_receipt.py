"""Add AP invoice auto inventory receipt fields.

Revision ID: 20260525_ap_invoice_auto_receipt
Revises: 20260525_repair_tax_txn_basis
Create Date: 2026-05-25
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260525_ap_invoice_auto_receipt"
down_revision = "20260525_repair_tax_txn_basis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "supplier_invoice",
        sa.Column(
            "auto_create_inventory_receipt",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        schema="ap",
    )
    op.add_column(
        "supplier_invoice_line",
        sa.Column(
            "receipt_warehouse_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        schema="ap",
    )
    op.add_column(
        "supplier_invoice_line",
        sa.Column("receipt_reference", sa.String(length=100), nullable=True),
        schema="ap",
    )
    op.add_column(
        "supplier_invoice_line",
        sa.Column("receipt_serial_numbers", postgresql.ARRAY(sa.Text()), nullable=True),
        schema="ap",
    )
    op.add_column(
        "supplier_invoice_line",
        sa.Column(
            "receipt_auto_generate_serials",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        schema="ap",
    )
    op.add_column(
        "supplier_invoice_line",
        sa.Column(
            "auto_receipt_transaction_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        schema="ap",
    )

    op.create_foreign_key(
        "fk_supplier_invoice_line_receipt_warehouse",
        "supplier_invoice_line",
        "warehouse",
        ["receipt_warehouse_id"],
        ["warehouse_id"],
        source_schema="ap",
        referent_schema="inv",
    )
    op.create_foreign_key(
        "fk_supplier_invoice_line_auto_receipt_txn",
        "supplier_invoice_line",
        "inventory_transaction",
        ["auto_receipt_transaction_id"],
        ["transaction_id"],
        source_schema="ap",
        referent_schema="inv",
    )
    op.create_unique_constraint(
        "uq_supplier_invoice_line_auto_receipt_txn",
        "supplier_invoice_line",
        ["auto_receipt_transaction_id"],
        schema="ap",
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_supplier_invoice_line_auto_receipt_txn",
        "supplier_invoice_line",
        schema="ap",
        type_="unique",
    )
    op.drop_constraint(
        "fk_supplier_invoice_line_auto_receipt_txn",
        "supplier_invoice_line",
        schema="ap",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_supplier_invoice_line_receipt_warehouse",
        "supplier_invoice_line",
        schema="ap",
        type_="foreignkey",
    )
    op.drop_column("supplier_invoice_line", "auto_receipt_transaction_id", schema="ap")
    op.drop_column(
        "supplier_invoice_line", "receipt_auto_generate_serials", schema="ap"
    )
    op.drop_column("supplier_invoice_line", "receipt_serial_numbers", schema="ap")
    op.drop_column("supplier_invoice_line", "receipt_reference", schema="ap")
    op.drop_column("supplier_invoice_line", "receipt_warehouse_id", schema="ap")
    op.drop_column("supplier_invoice", "auto_create_inventory_receipt", schema="ap")
