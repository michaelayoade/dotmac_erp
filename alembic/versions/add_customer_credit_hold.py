"""Add credit_hold column to ar.customer.

Revision ID: add_customer_credit_hold
Revises: add_module_integrations
Create Date: 2026-01-16

This migration adds the credit_hold column to the ar.customer table.
The model already has this field but the database was missing it.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "add_customer_credit_hold"
down_revision = "add_module_integrations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("customer", schema="ar"):
        columns = {c["name"] for c in inspector.get_columns("customer", schema="ar")}

        if "credit_hold" not in columns:
            op.add_column(
                "customer",
                sa.Column(
                    "credit_hold", sa.Boolean, nullable=False, server_default="false"
                ),
                schema="ar",
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("customer", schema="ar"):
        columns = {c["name"] for c in inspector.get_columns("customer", schema="ar")}

        if "credit_hold" in columns:
            op.drop_column("customer", "credit_hold", schema="ar")
