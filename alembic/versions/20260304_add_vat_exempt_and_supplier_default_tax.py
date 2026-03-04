"""Add VAT-exempt flag on Customer and default_tax_code_id on Supplier.

Adds:
- ar.customer.is_vat_exempt: boolean flag for VAT-exempt customers
- ap.supplier.default_tax_code_id: FK to tax.tax_code for default purchase tax

Revision ID: 20260304_add_vat_exempt_supplier_tax
Revises: 20260304_add_customer_parent_hierarchy
Create Date: 2026-03-04
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260304_add_vat_exempt_supplier_tax"
down_revision = "20260304_add_customer_parent_hierarchy"
branch_labels = None
depends_on = None


def _column_exists(schema: str, table: str, column: str) -> bool:
    """Check if a column exists (idempotent guard)."""
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = :table "
            "AND column_name = :column"
        ),
        {"schema": schema, "table": table, "column": column},
    )
    return result.scalar() is not None


def upgrade() -> None:
    # 1. Add is_vat_exempt to ar.customer
    if not _column_exists("ar", "customer", "is_vat_exempt"):
        op.add_column(
            "customer",
            sa.Column(
                "is_vat_exempt",
                sa.Boolean(),
                nullable=False,
                server_default="false",
                comment="Customer is exempt from VAT — invoice lines default to No Tax",
            ),
            schema="ar",
        )

    # 2. Add default_tax_code_id to ap.supplier
    if not _column_exists("ap", "supplier", "default_tax_code_id"):
        op.add_column(
            "supplier",
            sa.Column(
                "default_tax_code_id",
                UUID(as_uuid=True),
                nullable=True,
                comment="Default purchase tax code for this supplier",
            ),
            schema="ap",
        )
        op.create_foreign_key(
            "fk_supplier_default_tax_code",
            "supplier",
            "tax_code",
            ["default_tax_code_id"],
            ["tax_code_id"],
            source_schema="ap",
            referent_schema="tax",
        )


def downgrade() -> None:
    # Remove FK first, then column
    if _column_exists("ap", "supplier", "default_tax_code_id"):
        op.drop_constraint(
            "fk_supplier_default_tax_code", "supplier", schema="ap", type_="foreignkey"
        )
        op.drop_column("supplier", "default_tax_code_id", schema="ap")

    if _column_exists("ar", "customer", "is_vat_exempt"):
        op.drop_column("customer", "is_vat_exempt", schema="ar")
