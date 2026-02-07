"""Add default tax code to customers.

Revision ID: 20260207_add_customer_default_tax_code_id
Revises: 20260207_add_customer_vat_category
Create Date: 2026-02-07
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "20260207_add_customer_default_tax_code_id"
down_revision = "20260207_add_customer_vat_category"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("customer", schema="ar"):
        columns = {
            col["name"] for col in inspector.get_columns("customer", schema="ar")
        }
        if "default_tax_code_id" not in columns:
            op.add_column(
                "customer",
                sa.Column("default_tax_code_id", UUID(as_uuid=True), nullable=True),
                schema="ar",
            )

        fks = inspector.get_foreign_keys("customer", schema="ar")
        has_fk = any(
            fk.get("constrained_columns") == ["default_tax_code_id"]
            and fk.get("referred_table") == "tax_code"
            for fk in fks
        )
        if not has_fk:
            op.create_foreign_key(
                "fk_customer_default_tax_code",
                "customer",
                "tax_code",
                ["default_tax_code_id"],
                ["tax_code_id"],
                source_schema="ar",
                referent_schema="tax",
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("customer", schema="ar"):
        fks = inspector.get_foreign_keys("customer", schema="ar")
        has_fk = any(fk.get("name") == "fk_customer_default_tax_code" for fk in fks)
        if has_fk:
            op.drop_constraint(
                "fk_customer_default_tax_code",
                "customer",
                schema="ar",
                type_="foreignkey",
            )

        columns = {
            col["name"] for col in inspector.get_columns("customer", schema="ar")
        }
        if "default_tax_code_id" in columns:
            op.drop_column("customer", "default_tax_code_id", schema="ar")
