"""Add quote and sales order tables.

Revision ID: add_quote_and_sales_order
Revises: add_common_attachment
Create Date: 2025-02-04
"""

from alembic import op
from app.alembic_utils import ensure_enum
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID

from app.config import settings

# revision identifiers, used by Alembic.
revision = "add_quote_and_sales_order"
down_revision = "add_common_attachment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def table_exists(name: str, schema: str) -> bool:
        return inspector.has_table(name, schema=schema)

    def columns_exist(table_name: str, schema: str, columns: list[str]) -> bool:
        existing = {
            col["name"] for col in inspector.get_columns(table_name, schema=schema)
        }
        return all(col in existing for col in columns)

    def index_exists(
        name: str,
        table_name: str,
        schema: str,
        columns: list[str],
    ) -> bool:
        for idx in inspector.get_indexes(table_name, schema=schema):
            if idx.get("name") == name:
                return True
            if idx.get("column_names") == columns:
                return True
        return False

    def fk_exists(
        name: str,
        table_name: str,
        schema: str,
        constrained_columns: list[str],
        referred_table: str,
        referred_schema: str,
    ) -> bool:
        for fk in inspector.get_foreign_keys(table_name, schema=schema):
            if fk.get("name") == name:
                return True
            if fk.get("constrained_columns") != constrained_columns:
                continue
            if fk.get("referred_table") != referred_table:
                continue
            if fk.get("referred_schema") != referred_schema:
                continue
            return True
        return False

    # Create enum types
    ensure_enum(
        bind,
        "quote_status",
        "DRAFT",
        "SENT",
        "VIEWED",
        "ACCEPTED",
        "REJECTED",
        "EXPIRED",
        "CONVERTED",
        "VOID",
    )
    ensure_enum(
        bind,
        "so_status",
        "DRAFT",
        "SUBMITTED",
        "APPROVED",
        "CONFIRMED",
        "IN_PROGRESS",
        "SHIPPED",
        "COMPLETED",
        "CANCELLED",
        "ON_HOLD",
    )
    ensure_enum(
        bind,
        "so_fulfillment_status",
        "PENDING",
        "PARTIAL",
        "FULFILLED",
        "BACKORDERED",
        "CANCELLED",
    )

    # Create quote table
    if not table_exists("quote", "ar"):
        op.create_table(
            "quote",
            sa.Column(
                "quote_id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("organization_id", UUID(as_uuid=True), nullable=False),
            sa.Column("quote_number", sa.String(30), nullable=False),
            sa.Column("reference", sa.String(100), nullable=True),
            sa.Column(
                "customer_id",
                UUID(as_uuid=True),
                sa.ForeignKey("ar.customer.customer_id"),
                nullable=False,
            ),
            sa.Column("contact_name", sa.String(200), nullable=True),
            sa.Column("contact_email", sa.String(255), nullable=True),
            sa.Column("quote_date", sa.Date, nullable=False),
            sa.Column("valid_until", sa.Date, nullable=False),
            sa.Column(
                "subtotal", sa.Numeric(19, 4), nullable=False, server_default="0"
            ),
            sa.Column(
                "discount_amount", sa.Numeric(19, 4), nullable=False, server_default="0"
            ),
            sa.Column(
                "tax_amount", sa.Numeric(19, 4), nullable=False, server_default="0"
            ),
            sa.Column(
                "total_amount", sa.Numeric(19, 4), nullable=False, server_default="0"
            ),
            sa.Column(
                "currency_code",
                sa.String(3),
                nullable=False,
                server_default=settings.default_functional_currency_code,
            ),
            sa.Column(
                "exchange_rate", sa.Numeric(19, 10), nullable=False, server_default="1"
            ),
            sa.Column(
                "payment_terms_id",
                UUID(as_uuid=True),
                sa.ForeignKey("ar.payment_terms.payment_terms_id"),
                nullable=True,
            ),
            sa.Column("terms_and_conditions", sa.Text, nullable=True),
            sa.Column("internal_notes", sa.Text, nullable=True),
            sa.Column("customer_notes", sa.Text, nullable=True),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "DRAFT",
                    "SENT",
                    "VIEWED",
                    "ACCEPTED",
                    "REJECTED",
                    "EXPIRED",
                    "CONVERTED",
                    "VOID",
                    name="quote_status",
                    create_type=False,
                ),
                nullable=False,
                server_default="DRAFT",
            ),
            sa.Column(
                "converted_to_invoice_id",
                UUID(as_uuid=True),
                sa.ForeignKey("ar.invoice.invoice_id"),
                nullable=True,
            ),
            sa.Column("converted_to_so_id", UUID(as_uuid=True), nullable=True),
            sa.Column("converted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("sent_by", UUID(as_uuid=True), nullable=True),
            sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rejection_reason", sa.Text, nullable=True),
            sa.Column("created_by", UUID(as_uuid=True), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint(
                "organization_id", "quote_number", name="uq_quote_number"
            ),
            schema="ar",
        )
    if columns_exist("quote", "ar", ["organization_id", "status"]) and not index_exists(
        "idx_quote_org_status",
        "quote",
        "ar",
        ["organization_id", "status"],
    ):
        op.create_index(
            "idx_quote_org_status", "quote", ["organization_id", "status"], schema="ar"
        )
    if columns_exist("quote", "ar", ["customer_id"]) and not index_exists(
        "idx_quote_customer",
        "quote",
        "ar",
        ["customer_id"],
    ):
        op.create_index("idx_quote_customer", "quote", ["customer_id"], schema="ar")
    if columns_exist(
        "quote", "ar", ["organization_id", "quote_date"]
    ) and not index_exists(
        "idx_quote_date",
        "quote",
        "ar",
        ["organization_id", "quote_date"],
    ):
        op.create_index(
            "idx_quote_date", "quote", ["organization_id", "quote_date"], schema="ar"
        )

    # Create quote_line table
    if not table_exists("quote_line", "ar"):
        op.create_table(
            "quote_line",
            sa.Column(
                "line_id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "quote_id",
                UUID(as_uuid=True),
                sa.ForeignKey("ar.quote.quote_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("line_number", sa.Integer, nullable=False),
            sa.Column("item_code", sa.String(50), nullable=True),
            sa.Column("description", sa.String(500), nullable=False),
            sa.Column(
                "quantity", sa.Numeric(19, 4), nullable=False, server_default="1"
            ),
            sa.Column("unit_of_measure", sa.String(20), nullable=True),
            sa.Column("unit_price", sa.Numeric(19, 4), nullable=False),
            sa.Column(
                "discount_percent", sa.Numeric(5, 2), nullable=False, server_default="0"
            ),
            sa.Column(
                "discount_amount", sa.Numeric(19, 4), nullable=False, server_default="0"
            ),
            sa.Column(
                "tax_code_id",
                UUID(as_uuid=True),
                sa.ForeignKey("tax.tax_code.tax_code_id"),
                nullable=True,
            ),
            sa.Column(
                "tax_amount", sa.Numeric(19, 4), nullable=False, server_default="0"
            ),
            sa.Column("line_total", sa.Numeric(19, 4), nullable=False),
            sa.Column(
                "revenue_account_id",
                UUID(as_uuid=True),
                sa.ForeignKey("gl.account.account_id"),
                nullable=True,
            ),
            sa.Column(
                "project_id",
                UUID(as_uuid=True),
                sa.ForeignKey("core_org.project.project_id"),
                nullable=True,
            ),
            sa.Column(
                "cost_center_id",
                UUID(as_uuid=True),
                sa.ForeignKey("core_org.cost_center.cost_center_id"),
                nullable=True,
            ),
            schema="ar",
        )
    if columns_exist("quote_line", "ar", ["quote_id"]) and not index_exists(
        "idx_quote_line_quote",
        "quote_line",
        "ar",
        ["quote_id"],
    ):
        op.create_index("idx_quote_line_quote", "quote_line", ["quote_id"], schema="ar")

    # Create sales_order table
    if not table_exists("sales_order", "ar"):
        op.create_table(
            "sales_order",
            sa.Column(
                "so_id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("organization_id", UUID(as_uuid=True), nullable=False),
            sa.Column("so_number", sa.String(30), nullable=False),
            sa.Column("customer_po_number", sa.String(100), nullable=True),
            sa.Column("reference", sa.String(100), nullable=True),
            sa.Column(
                "customer_id",
                UUID(as_uuid=True),
                sa.ForeignKey("ar.customer.customer_id"),
                nullable=False,
            ),
            sa.Column(
                "quote_id",
                UUID(as_uuid=True),
                sa.ForeignKey("ar.quote.quote_id"),
                nullable=True,
            ),
            sa.Column("order_date", sa.Date, nullable=False),
            sa.Column("requested_date", sa.Date, nullable=True),
            sa.Column("promised_date", sa.Date, nullable=True),
            sa.Column("ship_to_name", sa.String(200), nullable=True),
            sa.Column("ship_to_address", sa.Text, nullable=True),
            sa.Column("ship_to_city", sa.String(100), nullable=True),
            sa.Column("ship_to_state", sa.String(100), nullable=True),
            sa.Column("ship_to_postal_code", sa.String(20), nullable=True),
            sa.Column("ship_to_country", sa.String(100), nullable=True),
            sa.Column("shipping_method", sa.String(100), nullable=True),
            sa.Column(
                "subtotal", sa.Numeric(19, 4), nullable=False, server_default="0"
            ),
            sa.Column(
                "discount_amount", sa.Numeric(19, 4), nullable=False, server_default="0"
            ),
            sa.Column(
                "shipping_amount", sa.Numeric(19, 4), nullable=False, server_default="0"
            ),
            sa.Column(
                "tax_amount", sa.Numeric(19, 4), nullable=False, server_default="0"
            ),
            sa.Column(
                "total_amount", sa.Numeric(19, 4), nullable=False, server_default="0"
            ),
            sa.Column(
                "invoiced_amount", sa.Numeric(19, 4), nullable=False, server_default="0"
            ),
            sa.Column(
                "currency_code",
                sa.String(3),
                nullable=False,
                server_default=settings.default_functional_currency_code,
            ),
            sa.Column(
                "exchange_rate", sa.Numeric(19, 10), nullable=False, server_default="1"
            ),
            sa.Column(
                "payment_terms_id",
                UUID(as_uuid=True),
                sa.ForeignKey("ar.payment_terms.payment_terms_id"),
                nullable=True,
            ),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "DRAFT",
                    "SUBMITTED",
                    "APPROVED",
                    "CONFIRMED",
                    "IN_PROGRESS",
                    "SHIPPED",
                    "COMPLETED",
                    "CANCELLED",
                    "ON_HOLD",
                    name="so_status",
                    create_type=False,
                ),
                nullable=False,
                server_default="DRAFT",
            ),
            sa.Column(
                "is_backorder", sa.Boolean, nullable=False, server_default="false"
            ),
            sa.Column(
                "allow_partial_shipment",
                sa.Boolean,
                nullable=False,
                server_default="true",
            ),
            sa.Column("internal_notes", sa.Text, nullable=True),
            sa.Column("customer_notes", sa.Text, nullable=True),
            sa.Column("submitted_by", UUID(as_uuid=True), nullable=True),
            sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("approved_by", UUID(as_uuid=True), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancellation_reason", sa.Text, nullable=True),
            sa.Column("created_by", UUID(as_uuid=True), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint(
                "organization_id", "so_number", name="uq_sales_order_number"
            ),
            schema="ar",
        )
    if columns_exist(
        "sales_order", "ar", ["organization_id", "status"]
    ) and not index_exists(
        "idx_so_org_status",
        "sales_order",
        "ar",
        ["organization_id", "status"],
    ):
        op.create_index(
            "idx_so_org_status",
            "sales_order",
            ["organization_id", "status"],
            schema="ar",
        )
    if columns_exist("sales_order", "ar", ["customer_id"]) and not index_exists(
        "idx_so_customer",
        "sales_order",
        "ar",
        ["customer_id"],
    ):
        op.create_index("idx_so_customer", "sales_order", ["customer_id"], schema="ar")
    if columns_exist(
        "sales_order", "ar", ["organization_id", "order_date"]
    ) and not index_exists(
        "idx_so_date",
        "sales_order",
        "ar",
        ["organization_id", "order_date"],
    ):
        op.create_index(
            "idx_so_date", "sales_order", ["organization_id", "order_date"], schema="ar"
        )

    # Add FK from quote to sales_order after SO exists
    if columns_exist("quote", "ar", ["converted_to_so_id"]) and not fk_exists(
        "fk_quote_converted_to_so",
        "quote",
        "ar",
        ["converted_to_so_id"],
        "sales_order",
        "ar",
    ):
        op.create_foreign_key(
            "fk_quote_converted_to_so",
            "quote",
            "sales_order",
            ["converted_to_so_id"],
            ["so_id"],
            source_schema="ar",
            referent_schema="ar",
        )

    # Create sales_order_line table
    if not table_exists("sales_order_line", "ar"):
        op.create_table(
            "sales_order_line",
            sa.Column(
                "line_id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "so_id",
                UUID(as_uuid=True),
                sa.ForeignKey("ar.sales_order.so_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("line_number", sa.Integer, nullable=False),
            sa.Column(
                "item_id",
                UUID(as_uuid=True),
                sa.ForeignKey("inv.item.item_id"),
                nullable=True,
            ),
            sa.Column("item_code", sa.String(50), nullable=True),
            sa.Column("description", sa.String(500), nullable=False),
            sa.Column("quantity_ordered", sa.Numeric(19, 4), nullable=False),
            sa.Column(
                "quantity_shipped",
                sa.Numeric(19, 4),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "quantity_invoiced",
                sa.Numeric(19, 4),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "quantity_backordered",
                sa.Numeric(19, 4),
                nullable=False,
                server_default="0",
            ),
            sa.Column("unit_of_measure", sa.String(20), nullable=True),
            sa.Column("unit_price", sa.Numeric(19, 4), nullable=False),
            sa.Column(
                "discount_percent", sa.Numeric(5, 2), nullable=False, server_default="0"
            ),
            sa.Column(
                "discount_amount", sa.Numeric(19, 4), nullable=False, server_default="0"
            ),
            sa.Column(
                "tax_code_id",
                UUID(as_uuid=True),
                sa.ForeignKey("tax.tax_code.tax_code_id"),
                nullable=True,
            ),
            sa.Column(
                "tax_amount", sa.Numeric(19, 4), nullable=False, server_default="0"
            ),
            sa.Column("line_total", sa.Numeric(19, 4), nullable=False),
            sa.Column(
                "fulfillment_status",
                postgresql.ENUM(
                    "PENDING",
                    "PARTIAL",
                    "FULFILLED",
                    "BACKORDERED",
                    "CANCELLED",
                    name="so_fulfillment_status",
                    create_type=False,
                ),
                nullable=False,
                server_default="PENDING",
            ),
            sa.Column(
                "revenue_account_id",
                UUID(as_uuid=True),
                sa.ForeignKey("gl.account.account_id"),
                nullable=True,
            ),
            sa.Column(
                "project_id",
                UUID(as_uuid=True),
                sa.ForeignKey("core_org.project.project_id"),
                nullable=True,
            ),
            sa.Column(
                "cost_center_id",
                UUID(as_uuid=True),
                sa.ForeignKey("core_org.cost_center.cost_center_id"),
                nullable=True,
            ),
            sa.Column("requested_date", sa.Date, nullable=True),
            sa.Column("promised_date", sa.Date, nullable=True),
            schema="ar",
        )
    if columns_exist("sales_order_line", "ar", ["so_id"]) and not index_exists(
        "idx_so_line_so",
        "sales_order_line",
        "ar",
        ["so_id"],
    ):
        op.create_index("idx_so_line_so", "sales_order_line", ["so_id"], schema="ar")

    # Create shipment table
    if not table_exists("shipment", "ar"):
        op.create_table(
            "shipment",
            sa.Column(
                "shipment_id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("organization_id", UUID(as_uuid=True), nullable=False),
            sa.Column("shipment_number", sa.String(30), nullable=False),
            sa.Column(
                "so_id",
                UUID(as_uuid=True),
                sa.ForeignKey("ar.sales_order.so_id"),
                nullable=False,
            ),
            sa.Column("shipment_date", sa.Date, nullable=False),
            sa.Column("carrier", sa.String(100), nullable=True),
            sa.Column("tracking_number", sa.String(100), nullable=True),
            sa.Column("shipping_method", sa.String(100), nullable=True),
            sa.Column("ship_to_name", sa.String(200), nullable=True),
            sa.Column("ship_to_address", sa.Text, nullable=True),
            sa.Column(
                "is_delivered", sa.Boolean, nullable=False, server_default="false"
            ),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("notes", sa.Text, nullable=True),
            sa.Column("created_by", UUID(as_uuid=True), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "organization_id", "shipment_number", name="uq_shipment_number"
            ),
            schema="ar",
        )
    if columns_exist("shipment", "ar", ["so_id"]) and not index_exists(
        "idx_shipment_so",
        "shipment",
        "ar",
        ["so_id"],
    ):
        op.create_index("idx_shipment_so", "shipment", ["so_id"], schema="ar")
    if columns_exist(
        "shipment", "ar", ["organization_id", "shipment_date"]
    ) and not index_exists(
        "idx_shipment_date",
        "shipment",
        "ar",
        ["organization_id", "shipment_date"],
    ):
        op.create_index(
            "idx_shipment_date",
            "shipment",
            ["organization_id", "shipment_date"],
            schema="ar",
        )

    # Create shipment_line table
    if not table_exists("shipment_line", "ar"):
        op.create_table(
            "shipment_line",
            sa.Column(
                "shipment_line_id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "shipment_id",
                UUID(as_uuid=True),
                sa.ForeignKey("ar.shipment.shipment_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "so_line_id",
                UUID(as_uuid=True),
                sa.ForeignKey("ar.sales_order_line.line_id"),
                nullable=False,
            ),
            sa.Column("quantity_shipped", sa.Numeric(19, 4), nullable=False),
            sa.Column("lot_number", sa.String(50), nullable=True),
            sa.Column("serial_number", sa.String(50), nullable=True),
            schema="ar",
        )
    if columns_exist("shipment_line", "ar", ["shipment_id"]) and not index_exists(
        "idx_shipment_line_shipment",
        "shipment_line",
        "ar",
        ["shipment_id"],
    ):
        op.create_index(
            "idx_shipment_line_shipment", "shipment_line", ["shipment_id"], schema="ar"
        )
    if columns_exist("shipment_line", "ar", ["so_line_id"]) and not index_exists(
        "idx_shipment_line_so_line",
        "shipment_line",
        "ar",
        ["so_line_id"],
    ):
        op.create_index(
            "idx_shipment_line_so_line", "shipment_line", ["so_line_id"], schema="ar"
        )


def downgrade() -> None:
    op.drop_table("shipment_line", schema="ar")
    op.drop_table("shipment", schema="ar")
    op.drop_table("sales_order_line", schema="ar")
    op.drop_constraint(
        "fk_quote_converted_to_so", "quote", schema="ar", type_="foreignkey"
    )
    op.drop_table("sales_order", schema="ar")
    op.drop_table("quote_line", schema="ar")
    op.drop_table("quote", schema="ar")

    op.execute("DROP TYPE IF EXISTS so_fulfillment_status")
    op.execute("DROP TYPE IF EXISTS so_status")
    op.execute("DROP TYPE IF EXISTS quote_status")
