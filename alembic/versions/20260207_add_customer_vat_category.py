"""Add VAT category to customers.

Revision ID: 20260207_add_customer_vat_category
Revises: 20260207_add_expense_claim_recipient_name
Create Date: 2026-02-07
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260207_add_customer_vat_category"
down_revision = "20260207_add_expense_claim_recipient_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("customer", schema="ar"):
        columns = {
            col["name"] for col in inspector.get_columns("customer", schema="ar")
        }
        if "vat_category" not in columns:
            op.add_column(
                "customer",
                sa.Column("vat_category", sa.String(50), nullable=True),
                schema="ar",
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("customer", schema="ar"):
        columns = {
            col["name"] for col in inspector.get_columns("customer", schema="ar")
        }
        if "vat_category" in columns:
            op.drop_column("customer", "vat_category", schema="ar")
