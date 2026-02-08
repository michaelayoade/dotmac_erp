"""Add inventory extensions (price lists, BOM, counts, lot fields).

Revision ID: add_inventory_extensions
Revises: add_numbering_sequence_columns, add_organization_settings_columns
Create Date: 2025-02-15
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID

from alembic import op
from app.alembic_utils import ensure_enum

# revision identifiers, used by Alembic.
revision = "add_inventory_extensions"
down_revision = ("add_numbering_sequence_columns", "add_organization_settings_columns")
branch_labels = None
depends_on = None


def _index_names(inspector, table_name: str, schema: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table_name, schema=schema)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Enums
    ensure_enum(bind, "price_list_type", "SALES", "PURCHASE")
    ensure_enum(bind, "bom_type", "ASSEMBLY", "KIT", "PHANTOM")
    ensure_enum(
        bind, "count_status", "DRAFT", "IN_PROGRESS", "COMPLETED", "POSTED", "CANCELLED"
    )

    # Inventory lot enhancements
    if inspector.has_table("inventory_lot", schema="inv"):
        lot_columns = {
            column["name"]
            for column in inspector.get_columns("inventory_lot", schema="inv")
        }
        lot_indexes = _index_names(inspector, "inventory_lot", schema="inv")

        if "organization_id" not in lot_columns:
            op.add_column(
                "inventory_lot",
                sa.Column("organization_id", UUID(as_uuid=True), nullable=True),
                schema="inv",
            )
            op.create_foreign_key(
                "fk_inventory_lot_org",
                "inventory_lot",
                "organization",
                ["organization_id"],
                ["organization_id"],
                source_schema="inv",
                referent_schema="core_org",
            )
            op.execute(
                """
                UPDATE inv.inventory_lot AS lot
                SET organization_id = item.organization_id
                FROM inv.item AS item
                WHERE lot.item_id = item.item_id
                  AND lot.organization_id IS NULL
                """
            )
            op.alter_column(
                "inventory_lot",
                "organization_id",
                nullable=False,
                schema="inv",
            )
            lot_columns.add("organization_id")

        if "warehouse_id" not in lot_columns:
            op.add_column(
                "inventory_lot",
                sa.Column("warehouse_id", UUID(as_uuid=True), nullable=True),
                schema="inv",
            )
            op.create_foreign_key(
                "fk_inventory_lot_warehouse",
                "inventory_lot",
                "warehouse",
                ["warehouse_id"],
                ["warehouse_id"],
                source_schema="inv",
                referent_schema="inv",
            )
            lot_columns.add("warehouse_id")

        if "allocation_reference" not in lot_columns:
            op.add_column(
                "inventory_lot",
                sa.Column("allocation_reference", sa.String(100), nullable=True),
                schema="inv",
            )

        if "quantity_available" in lot_columns:
            op.execute(
                """
                UPDATE inv.inventory_lot
                SET quantity_available = quantity_on_hand - COALESCE(quantity_allocated, 0)
                WHERE quantity_available IS NULL
                """
            )

        if "idx_lot_org" not in lot_indexes and "organization_id" in lot_columns:
            op.create_index(
                "idx_lot_org",
                "inventory_lot",
                ["organization_id"],
                schema="inv",
            )
        if "idx_lot_warehouse" not in lot_indexes and "warehouse_id" in lot_columns:
            op.create_index(
                "idx_lot_warehouse",
                "inventory_lot",
                ["warehouse_id"],
                schema="inv",
            )

    # Price list tables
    if not inspector.has_table("price_list", schema="inv"):
        op.create_table(
            "price_list",
            sa.Column(
                "price_list_id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("organization_id", UUID(as_uuid=True), nullable=False),
            sa.Column("price_list_code", sa.String(30), nullable=False),
            sa.Column("price_list_name", sa.String(100), nullable=False),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column(
                "price_list_type",
                postgresql.ENUM(
                    "SALES",
                    "PURCHASE",
                    name="price_list_type",
                    create_type=False,
                ),
                nullable=False,
                server_default="SALES",
            ),
            sa.Column("currency_code", sa.String(3), nullable=False),
            sa.Column("effective_from", sa.Date, nullable=True),
            sa.Column("effective_to", sa.Date, nullable=True),
            sa.Column("priority", sa.Numeric(5, 0), nullable=False, server_default="0"),
            sa.Column("base_price_list_id", UUID(as_uuid=True), nullable=True),
            sa.Column("markup_percent", sa.Numeric(10, 4), nullable=True),
            sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["organization_id"],
                ["core_org.organization.organization_id"],
                name="fk_price_list_org",
            ),
            sa.ForeignKeyConstraint(
                ["base_price_list_id"],
                ["inv.price_list.price_list_id"],
                name="fk_price_list_base",
            ),
            sa.UniqueConstraint(
                "organization_id",
                "price_list_code",
                name="uq_price_list_code",
            ),
            schema="inv",
        )
        op.create_index(
            "idx_price_list_type",
            "price_list",
            ["price_list_type"],
            schema="inv",
        )

    if not inspector.has_table("price_list_item", schema="inv"):
        op.create_table(
            "price_list_item",
            sa.Column(
                "price_list_item_id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("price_list_id", UUID(as_uuid=True), nullable=False),
            sa.Column("item_id", UUID(as_uuid=True), nullable=False),
            sa.Column("unit_price", sa.Numeric(20, 6), nullable=False),
            sa.Column("currency_code", sa.String(3), nullable=False),
            sa.Column(
                "min_quantity", sa.Numeric(20, 6), nullable=False, server_default="1"
            ),
            sa.Column("discount_percent", sa.Numeric(10, 4), nullable=True),
            sa.Column("discount_amount", sa.Numeric(20, 6), nullable=True),
            sa.Column("effective_from", sa.Date, nullable=True),
            sa.Column("effective_to", sa.Date, nullable=True),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["price_list_id"],
                ["inv.price_list.price_list_id"],
                name="fk_price_list_item_list",
            ),
            sa.ForeignKeyConstraint(
                ["item_id"],
                ["inv.item.item_id"],
                name="fk_price_list_item_item",
            ),
            schema="inv",
        )
        op.create_index(
            "idx_price_list_item_list",
            "price_list_item",
            ["price_list_id"],
            schema="inv",
        )
        op.create_index(
            "idx_price_list_item_item",
            "price_list_item",
            ["item_id"],
            schema="inv",
        )

    # BOM tables
    if not inspector.has_table("bill_of_materials", schema="inv"):
        op.create_table(
            "bill_of_materials",
            sa.Column(
                "bom_id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("organization_id", UUID(as_uuid=True), nullable=False),
            sa.Column("bom_code", sa.String(30), nullable=False),
            sa.Column("bom_name", sa.String(100), nullable=False),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column("item_id", UUID(as_uuid=True), nullable=False),
            sa.Column(
                "bom_type",
                postgresql.ENUM(
                    "ASSEMBLY",
                    "KIT",
                    "PHANTOM",
                    name="bom_type",
                    create_type=False,
                ),
                nullable=False,
                server_default="ASSEMBLY",
            ),
            sa.Column(
                "output_quantity", sa.Numeric(20, 6), nullable=False, server_default="1"
            ),
            sa.Column("output_uom", sa.String(20), nullable=False),
            sa.Column("version", sa.Numeric(5, 0), nullable=False, server_default="1"),
            sa.Column("is_default", sa.Boolean, nullable=False, server_default="true"),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["organization_id"],
                ["core_org.organization.organization_id"],
                name="fk_bom_org",
            ),
            sa.ForeignKeyConstraint(
                ["item_id"],
                ["inv.item.item_id"],
                name="fk_bom_item",
            ),
            sa.UniqueConstraint("organization_id", "bom_code", name="uq_bom_code"),
            schema="inv",
        )
        op.create_index(
            "idx_bom_item",
            "bill_of_materials",
            ["item_id"],
            schema="inv",
        )

    if not inspector.has_table("bom_component", schema="inv"):
        op.create_table(
            "bom_component",
            sa.Column(
                "component_id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("bom_id", UUID(as_uuid=True), nullable=False),
            sa.Column("component_item_id", UUID(as_uuid=True), nullable=False),
            sa.Column("quantity", sa.Numeric(20, 6), nullable=False),
            sa.Column("uom", sa.String(20), nullable=False),
            sa.Column(
                "scrap_percent", sa.Numeric(10, 4), nullable=False, server_default="0"
            ),
            sa.Column(
                "line_number", sa.Numeric(5, 0), nullable=False, server_default="1"
            ),
            sa.Column("warehouse_id", UUID(as_uuid=True), nullable=True),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(
                ["bom_id"],
                ["inv.bill_of_materials.bom_id"],
                name="fk_bom_component_bom",
            ),
            sa.ForeignKeyConstraint(
                ["component_item_id"],
                ["inv.item.item_id"],
                name="fk_bom_component_item",
            ),
            schema="inv",
        )
        op.create_index(
            "idx_bom_component_bom",
            "bom_component",
            ["bom_id"],
            schema="inv",
        )
        op.create_index(
            "idx_bom_component_item",
            "bom_component",
            ["component_item_id"],
            schema="inv",
        )

    # Inventory count tables
    if not inspector.has_table("inventory_count", schema="inv"):
        op.create_table(
            "inventory_count",
            sa.Column(
                "count_id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("organization_id", UUID(as_uuid=True), nullable=False),
            sa.Column("count_number", sa.String(30), nullable=False),
            sa.Column("count_description", sa.Text, nullable=True),
            sa.Column("count_date", sa.Date, nullable=False),
            sa.Column("fiscal_period_id", UUID(as_uuid=True), nullable=False),
            sa.Column("warehouse_id", UUID(as_uuid=True), nullable=True),
            sa.Column("location_id", UUID(as_uuid=True), nullable=True),
            sa.Column("category_id", UUID(as_uuid=True), nullable=True),
            sa.Column(
                "is_full_count", sa.Boolean, nullable=False, server_default="false"
            ),
            sa.Column(
                "is_cycle_count", sa.Boolean, nullable=False, server_default="false"
            ),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "DRAFT",
                    "IN_PROGRESS",
                    "COMPLETED",
                    "POSTED",
                    "CANCELLED",
                    name="count_status",
                    create_type=False,
                ),
                nullable=False,
                server_default="DRAFT",
            ),
            sa.Column("total_items", sa.Integer, nullable=False, server_default="0"),
            sa.Column("items_counted", sa.Integer, nullable=False, server_default="0"),
            sa.Column(
                "items_with_variance", sa.Integer, nullable=False, server_default="0"
            ),
            sa.Column("adjustment_journal_entry_id", UUID(as_uuid=True), nullable=True),
            sa.Column("created_by_user_id", UUID(as_uuid=True), nullable=False),
            sa.Column("approved_by_user_id", UUID(as_uuid=True), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("posted_by_user_id", UUID(as_uuid=True), nullable=True),
            sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["organization_id"],
                ["core_org.organization.organization_id"],
                name="fk_inventory_count_org",
            ),
            sa.ForeignKeyConstraint(
                ["fiscal_period_id"],
                ["gl.fiscal_period.fiscal_period_id"],
                name="fk_inventory_count_period",
            ),
            sa.ForeignKeyConstraint(
                ["warehouse_id"],
                ["inv.warehouse.warehouse_id"],
                name="fk_inventory_count_warehouse",
            ),
            sa.UniqueConstraint(
                "organization_id",
                "count_number",
                name="uq_inventory_count",
            ),
            schema="inv",
        )

    if not inspector.has_table("inventory_count_line", schema="inv"):
        op.create_table(
            "inventory_count_line",
            sa.Column(
                "line_id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("count_id", UUID(as_uuid=True), nullable=False),
            sa.Column("item_id", UUID(as_uuid=True), nullable=False),
            sa.Column("warehouse_id", UUID(as_uuid=True), nullable=False),
            sa.Column("location_id", UUID(as_uuid=True), nullable=True),
            sa.Column("lot_id", UUID(as_uuid=True), nullable=True),
            sa.Column("system_quantity", sa.Numeric(20, 6), nullable=False),
            sa.Column("uom", sa.String(20), nullable=False),
            sa.Column("counted_quantity", sa.Numeric(20, 6), nullable=True),
            sa.Column("recount_quantity", sa.Numeric(20, 6), nullable=True),
            sa.Column("final_quantity", sa.Numeric(20, 6), nullable=True),
            sa.Column("variance_quantity", sa.Numeric(20, 6), nullable=True),
            sa.Column("variance_value", sa.Numeric(20, 6), nullable=True),
            sa.Column("variance_percent", sa.Numeric(10, 4), nullable=True),
            sa.Column("unit_cost", sa.Numeric(20, 6), nullable=False),
            sa.Column("counted_by_user_id", UUID(as_uuid=True), nullable=True),
            sa.Column("counted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("recounted_by_user_id", UUID(as_uuid=True), nullable=True),
            sa.Column("recounted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("reason_code", sa.String(30), nullable=True),
            sa.Column("notes", sa.Text, nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["count_id"],
                ["inv.inventory_count.count_id"],
                name="fk_inventory_count_line_count",
            ),
            sa.ForeignKeyConstraint(
                ["item_id"],
                ["inv.item.item_id"],
                name="fk_inventory_count_line_item",
            ),
            sa.ForeignKeyConstraint(
                ["warehouse_id"],
                ["inv.warehouse.warehouse_id"],
                name="fk_inventory_count_line_warehouse",
            ),
            sa.UniqueConstraint(
                "count_id",
                "item_id",
                "lot_id",
                name="uq_inventory_count_line",
            ),
            schema="inv",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("inventory_count_line", schema="inv"):
        op.drop_table("inventory_count_line", schema="inv")

    if inspector.has_table("inventory_count", schema="inv"):
        op.drop_table("inventory_count", schema="inv")

    if inspector.has_table("bom_component", schema="inv"):
        op.drop_table("bom_component", schema="inv")

    if inspector.has_table("bill_of_materials", schema="inv"):
        op.drop_table("bill_of_materials", schema="inv")

    if inspector.has_table("price_list_item", schema="inv"):
        op.drop_table("price_list_item", schema="inv")

    if inspector.has_table("price_list", schema="inv"):
        op.drop_table("price_list", schema="inv")

    if inspector.has_table("inventory_lot", schema="inv"):
        lot_columns = {
            column["name"]
            for column in inspector.get_columns("inventory_lot", schema="inv")
        }
        lot_indexes = _index_names(inspector, "inventory_lot", schema="inv")

        if "idx_lot_warehouse" in lot_indexes:
            op.drop_index("idx_lot_warehouse", table_name="inventory_lot", schema="inv")
        if "idx_lot_org" in lot_indexes:
            op.drop_index("idx_lot_org", table_name="inventory_lot", schema="inv")

        if "allocation_reference" in lot_columns:
            op.drop_column("inventory_lot", "allocation_reference", schema="inv")
        if "warehouse_id" in lot_columns:
            op.drop_column("inventory_lot", "warehouse_id", schema="inv")
        if "organization_id" in lot_columns:
            op.drop_column("inventory_lot", "organization_id", schema="inv")

    sa.Enum(name="price_list_type").drop(bind, checkfirst=True)
    sa.Enum(name="bom_type").drop(bind, checkfirst=True)
    sa.Enum(name="count_status").drop(bind, checkfirst=True)
