"""Add flexible tax support tables and columns.

Revision ID: add_flexible_tax_support
Revises: add_audit_user_columns
Create Date: 2026-01-16

This migration adds:
- ar.invoice_line_tax table (multiple taxes per invoice line)
- ap.supplier_invoice_line_tax table (multiple taxes per supplier invoice line)
- WHT fields to ar.customer (withholding tax configuration)
- WHT fields to ar.customer_payment (WHT on receipts)
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "add_flexible_tax_support"
down_revision = "add_audit_user_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def has_table(schema: str, table: str) -> bool:
        return inspector.has_table(table, schema=schema)

    def has_column(schema: str, table: str, column: str) -> bool:
        if not has_table(schema, table):
            return False
        return any(col["name"] == column for col in inspector.get_columns(table, schema=schema))

    def has_fk(schema: str, table: str, fk_name: str) -> bool:
        if not has_table(schema, table):
            return False
        return any(fk.get("name") == fk_name for fk in inspector.get_foreign_keys(table, schema=schema))

    # Create AR Invoice Line Tax table
    if not has_table("ar", "invoice_line_tax"):
        op.create_table(
            "invoice_line_tax",
            sa.Column(
                "line_tax_id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "line_id",
                UUID(as_uuid=True),
                sa.ForeignKey("ar.invoice_line.line_id"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "tax_code_id",
                UUID(as_uuid=True),
                sa.ForeignKey("tax.tax_code.tax_code_id"),
                nullable=False,
            ),
            sa.Column("base_amount", sa.Numeric(20, 6), nullable=False),
            sa.Column(
                "tax_rate",
                sa.Numeric(10, 6),
                nullable=False,
                comment="Rate snapshot at invoice time",
            ),
            sa.Column("tax_amount", sa.Numeric(20, 6), nullable=False),
            sa.Column("is_inclusive", sa.Boolean, nullable=False, default=False),
            sa.Column(
                "sequence",
                sa.Integer,
                nullable=False,
                default=1,
                comment="Order for compound tax calculation",
            ),
            sa.Column(
                "is_recoverable",
                sa.Boolean,
                nullable=False,
                default=True,
                comment="For special cases like bad debt VAT relief",
            ),
            sa.Column(
                "recoverable_amount",
                sa.Numeric(20, 6),
                nullable=False,
                default=0,
                comment="Amount eligible for special treatment",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            schema="ar",
        )

    # Create AP Supplier Invoice Line Tax table
    if not has_table("ap", "supplier_invoice_line_tax"):
        op.create_table(
            "supplier_invoice_line_tax",
            sa.Column(
                "line_tax_id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "line_id",
                UUID(as_uuid=True),
                sa.ForeignKey("ap.supplier_invoice_line.line_id"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "tax_code_id",
                UUID(as_uuid=True),
                sa.ForeignKey("tax.tax_code.tax_code_id"),
                nullable=False,
            ),
            sa.Column("base_amount", sa.Numeric(20, 6), nullable=False),
            sa.Column(
                "tax_rate",
                sa.Numeric(10, 6),
                nullable=False,
                comment="Rate snapshot at invoice time",
            ),
            sa.Column("tax_amount", sa.Numeric(20, 6), nullable=False),
            sa.Column("is_inclusive", sa.Boolean, nullable=False, default=False),
            sa.Column(
                "sequence",
                sa.Integer,
                nullable=False,
                default=1,
                comment="Order for compound tax calculation",
            ),
            sa.Column(
                "is_recoverable",
                sa.Boolean,
                nullable=False,
                default=True,
                comment="Can this input tax be recovered?",
            ),
            sa.Column(
                "recoverable_amount",
                sa.Numeric(20, 6),
                nullable=False,
                default=0,
                comment="Amount that can be recovered/claimed",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            schema="ap",
        )

    # Add WHT columns to ar.customer
    if not has_column("ar", "customer", "is_wht_applicable"):
        op.add_column(
            "customer",
            sa.Column(
                "is_wht_applicable",
                sa.Boolean,
                nullable=False,
                server_default=sa.false(),
                comment="Customer deducts WHT on payments to us",
            ),
            schema="ar",
        )
    if not has_column("ar", "customer", "default_wht_code_id"):
        op.add_column(
            "customer",
            sa.Column(
                "default_wht_code_id",
                UUID(as_uuid=True),
                nullable=True,
                comment="Default WHT rate for this customer",
            ),
            schema="ar",
        )
    if not has_column("ar", "customer", "wht_exemption_certificate"):
        op.add_column(
            "customer",
            sa.Column(
                "wht_exemption_certificate",
                sa.String(100),
                nullable=True,
                comment="WHT exemption certificate number",
            ),
            schema="ar",
        )
    if not has_column("ar", "customer", "wht_exemption_expiry"):
        op.add_column(
            "customer",
            sa.Column(
                "wht_exemption_expiry",
                sa.Date,
                nullable=True,
                comment="When WHT exemption expires",
            ),
            schema="ar",
        )

    # Add WHT columns to ar.customer_payment
    if not has_column("ar", "customer_payment", "gross_amount"):
        op.add_column(
            "customer_payment",
            sa.Column(
                "gross_amount",
                sa.Numeric(20, 6),
                nullable=True,  # Initially nullable for existing data
                comment="Amount before WHT deduction",
            ),
            schema="ar",
        )
    if not has_column("ar", "customer_payment", "wht_code_id"):
        op.add_column(
            "customer_payment",
            sa.Column(
                "wht_code_id",
                UUID(as_uuid=True),
                nullable=True,
                comment="WHT rate applied by customer",
            ),
            schema="ar",
        )
    if not has_column("ar", "customer_payment", "wht_amount"):
        op.add_column(
            "customer_payment",
            sa.Column(
                "wht_amount",
                sa.Numeric(20, 6),
                nullable=False,
                server_default="0",
                comment="WHT deducted by customer",
            ),
            schema="ar",
        )
    if not has_column("ar", "customer_payment", "wht_certificate_number"):
        op.add_column(
            "customer_payment",
            sa.Column(
                "wht_certificate_number",
                sa.String(100),
                nullable=True,
                comment="WHT certificate number received from customer",
            ),
            schema="ar",
        )

    # Update existing customer_payment rows: set gross_amount = amount (no WHT)
    if has_column("ar", "customer_payment", "gross_amount"):
        op.execute(
            "UPDATE ar.customer_payment SET gross_amount = amount WHERE gross_amount IS NULL"
        )

    # Now make gross_amount NOT NULL
    if has_column("ar", "customer_payment", "gross_amount"):
        op.alter_column(
            "customer_payment",
            "gross_amount",
            nullable=False,
            schema="ar",
        )

    # Add WHT columns to ap.supplier_payment
    if not has_column("ap", "supplier_payment", "withholding_tax_code_id"):
        op.add_column(
            "supplier_payment",
            sa.Column(
                "withholding_tax_code_id",
                UUID(as_uuid=True),
                nullable=True,
                comment="WHT tax code applied when withholding from supplier",
            ),
            schema="ap",
        )
    if not has_column("ap", "supplier_payment", "gross_amount"):
        op.add_column(
            "supplier_payment",
            sa.Column(
                "gross_amount",
                sa.Numeric(20, 6),
                nullable=True,
                comment="Invoice amount before WHT deduction",
            ),
            schema="ap",
        )

    # Create FK constraint for withholding_tax_code_id
    if not has_fk("ap", "supplier_payment", "fk_supplier_payment_wht_code"):
        op.create_foreign_key(
            "fk_supplier_payment_wht_code",
            "supplier_payment",
            "tax_code",
            ["withholding_tax_code_id"],
            ["tax_code_id"],
            source_schema="ap",
            referent_schema="tax",
        )

    # Update existing supplier_payment rows: set gross_amount = amount + withholding_tax_amount
    if has_column("ap", "supplier_payment", "gross_amount"):
        op.execute(
            "UPDATE ap.supplier_payment SET gross_amount = amount + withholding_tax_amount WHERE gross_amount IS NULL"
        )


def downgrade() -> None:
    # Remove WHT columns from ap.supplier_payment
    op.drop_constraint("fk_supplier_payment_wht_code", "supplier_payment", schema="ap", type_="foreignkey")
    op.drop_column("supplier_payment", "gross_amount", schema="ap")
    op.drop_column("supplier_payment", "withholding_tax_code_id", schema="ap")

    # Remove WHT columns from ar.customer_payment
    op.drop_column("customer_payment", "wht_certificate_number", schema="ar")
    op.drop_column("customer_payment", "wht_amount", schema="ar")
    op.drop_column("customer_payment", "wht_code_id", schema="ar")
    op.drop_column("customer_payment", "gross_amount", schema="ar")

    # Remove WHT columns from ar.customer
    op.drop_column("customer", "wht_exemption_expiry", schema="ar")
    op.drop_column("customer", "wht_exemption_certificate", schema="ar")
    op.drop_column("customer", "default_wht_code_id", schema="ar")
    op.drop_column("customer", "is_wht_applicable", schema="ar")

    # Drop AP Supplier Invoice Line Tax table
    op.drop_table("supplier_invoice_line_tax", schema="ap")

    # Drop AR Invoice Line Tax table
    op.drop_table("invoice_line_tax", schema="ar")
