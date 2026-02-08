"""Add module integrations columns for AP→INV, AP→FA, AR→INV.

Revision ID: add_module_integrations
Revises: add_flexible_tax_support
Create Date: 2026-01-16

This migration adds:
- AP→FA: asset_category_id and created_asset_id on ap.supplier_invoice_line
- AR→INV: warehouse_id, lot_id, and inventory_transaction_id on ar.invoice_line
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "add_module_integrations"
down_revision = "5c7e3587f12a"
branch_labels = None
depends_on = None


def _get_columns(inspector, table_name: str, schema: str) -> set[str]:
    """Get set of column names for a table."""
    return {
        column["name"] for column in inspector.get_columns(table_name, schema=schema)
    }


def _index_names(inspector, table_name: str, schema: str) -> set[str]:
    """Get set of index names for a table."""
    return {index["name"] for index in inspector.get_indexes(table_name, schema=schema)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # =================================================================
    # AP → Fixed Assets Integration
    # Add asset_category_id and created_asset_id to supplier_invoice_line
    # =================================================================
    if inspector.has_table("supplier_invoice_line", schema="ap"):
        ap_line_columns = _get_columns(inspector, "supplier_invoice_line", "ap")
        ap_line_indexes = _index_names(inspector, "supplier_invoice_line", "ap")

        # Add asset_category_id for capitalization
        if "asset_category_id" not in ap_line_columns:
            op.add_column(
                "supplier_invoice_line",
                sa.Column("asset_category_id", UUID(as_uuid=True), nullable=True),
                schema="ap",
            )
            # Add FK to fa.asset_category
            op.create_foreign_key(
                "fk_supplier_invoice_line_asset_category",
                "supplier_invoice_line",
                "asset_category",
                ["asset_category_id"],
                ["category_id"],
                source_schema="ap",
                referent_schema="fa",
            )

        # Add created_asset_id to track which asset was created from this line
        if "created_asset_id" not in ap_line_columns:
            op.add_column(
                "supplier_invoice_line",
                sa.Column("created_asset_id", UUID(as_uuid=True), nullable=True),
                schema="ap",
            )
            # Add FK to fa.asset
            op.create_foreign_key(
                "fk_supplier_invoice_line_created_asset",
                "supplier_invoice_line",
                "asset",
                ["created_asset_id"],
                ["asset_id"],
                source_schema="ap",
                referent_schema="fa",
            )

        # Add index for asset_category lookups
        if "idx_supplier_invoice_line_asset_cat" not in ap_line_indexes:
            op.create_index(
                "idx_supplier_invoice_line_asset_cat",
                "supplier_invoice_line",
                ["asset_category_id"],
                schema="ap",
            )

    # =================================================================
    # AR → Inventory Integration
    # Add warehouse_id, lot_id, and inventory_transaction_id to invoice_line
    # =================================================================
    if inspector.has_table("invoice_line", schema="ar"):
        ar_line_columns = _get_columns(inspector, "invoice_line", "ar")
        ar_line_indexes = _index_names(inspector, "invoice_line", "ar")

        # Add warehouse_id for inventory tracking
        if "warehouse_id" not in ar_line_columns:
            op.add_column(
                "invoice_line",
                sa.Column("warehouse_id", UUID(as_uuid=True), nullable=True),
                schema="ar",
            )
            # Add FK to inv.warehouse
            op.create_foreign_key(
                "fk_invoice_line_warehouse",
                "invoice_line",
                "warehouse",
                ["warehouse_id"],
                ["warehouse_id"],
                source_schema="ar",
                referent_schema="inv",
            )

        # Add lot_id for lot tracking
        if "lot_id" not in ar_line_columns:
            op.add_column(
                "invoice_line",
                sa.Column("lot_id", UUID(as_uuid=True), nullable=True),
                schema="ar",
            )
            # Add FK to inv.inventory_lot
            op.create_foreign_key(
                "fk_invoice_line_lot",
                "invoice_line",
                "inventory_lot",
                ["lot_id"],
                ["lot_id"],
                source_schema="ar",
                referent_schema="inv",
            )

        # Add inventory_transaction_id for traceability
        if "inventory_transaction_id" not in ar_line_columns:
            op.add_column(
                "invoice_line",
                sa.Column(
                    "inventory_transaction_id", UUID(as_uuid=True), nullable=True
                ),
                schema="ar",
            )
            # Add FK to inv.inventory_transaction
            op.create_foreign_key(
                "fk_invoice_line_inv_txn",
                "invoice_line",
                "inventory_transaction",
                ["inventory_transaction_id"],
                ["transaction_id"],
                source_schema="ar",
                referent_schema="inv",
            )

        # Add indexes for efficient lookups
        if "idx_invoice_line_warehouse" not in ar_line_indexes:
            op.create_index(
                "idx_invoice_line_warehouse",
                "invoice_line",
                ["warehouse_id"],
                schema="ar",
            )

        if "idx_invoice_line_lot" not in ar_line_indexes:
            op.create_index(
                "idx_invoice_line_lot",
                "invoice_line",
                ["lot_id"],
                schema="ar",
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Drop AR invoice_line columns
    if inspector.has_table("invoice_line", schema="ar"):
        ar_line_columns = _get_columns(inspector, "invoice_line", "ar")
        ar_line_indexes = _index_names(inspector, "invoice_line", "ar")

        if "idx_invoice_line_lot" in ar_line_indexes:
            op.drop_index(
                "idx_invoice_line_lot", table_name="invoice_line", schema="ar"
            )

        if "idx_invoice_line_warehouse" in ar_line_indexes:
            op.drop_index(
                "idx_invoice_line_warehouse", table_name="invoice_line", schema="ar"
            )

        if "inventory_transaction_id" in ar_line_columns:
            op.drop_constraint(
                "fk_invoice_line_inv_txn",
                "invoice_line",
                schema="ar",
                type_="foreignkey",
            )
            op.drop_column("invoice_line", "inventory_transaction_id", schema="ar")

        if "lot_id" in ar_line_columns:
            op.drop_constraint(
                "fk_invoice_line_lot",
                "invoice_line",
                schema="ar",
                type_="foreignkey",
            )
            op.drop_column("invoice_line", "lot_id", schema="ar")

        if "warehouse_id" in ar_line_columns:
            op.drop_constraint(
                "fk_invoice_line_warehouse",
                "invoice_line",
                schema="ar",
                type_="foreignkey",
            )
            op.drop_column("invoice_line", "warehouse_id", schema="ar")

    # Drop AP supplier_invoice_line columns
    if inspector.has_table("supplier_invoice_line", schema="ap"):
        ap_line_columns = _get_columns(inspector, "supplier_invoice_line", "ap")
        ap_line_indexes = _index_names(inspector, "supplier_invoice_line", "ap")

        if "idx_supplier_invoice_line_asset_cat" in ap_line_indexes:
            op.drop_index(
                "idx_supplier_invoice_line_asset_cat",
                table_name="supplier_invoice_line",
                schema="ap",
            )

        if "created_asset_id" in ap_line_columns:
            op.drop_constraint(
                "fk_supplier_invoice_line_created_asset",
                "supplier_invoice_line",
                schema="ap",
                type_="foreignkey",
            )
            op.drop_column("supplier_invoice_line", "created_asset_id", schema="ap")

        if "asset_category_id" in ap_line_columns:
            op.drop_constraint(
                "fk_supplier_invoice_line_asset_category",
                "supplier_invoice_line",
                schema="ap",
                type_="foreignkey",
            )
            op.drop_column("supplier_invoice_line", "asset_category_id", schema="ap")
