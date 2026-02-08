"""Add reorder defaults to item categories.

Revision ID: 20260206_add_item_category_reorder_levels
Revises: 20260206_add_workflow_entity_material_request
Create Date: 2026-02-06
"""

import sqlalchemy as sa

from alembic import op

revision = "20260206_add_item_category_reorder_levels"
down_revision = "20260206_add_workflow_entity_material_request"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add reorder defaults to inv.item_category."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("item_category", schema="inv"):
        return

    columns = {
        col["name"] for col in inspector.get_columns("item_category", schema="inv")
    }

    if "reorder_point" not in columns:
        op.add_column(
            "item_category",
            sa.Column("reorder_point", sa.Numeric(20, 6), nullable=True),
            schema="inv",
        )
    if "minimum_stock" not in columns:
        op.add_column(
            "item_category",
            sa.Column("minimum_stock", sa.Numeric(20, 6), nullable=True),
            schema="inv",
        )


def downgrade() -> None:
    """Remove reorder defaults from inv.item_category."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("item_category", schema="inv"):
        return

    columns = {
        col["name"] for col in inspector.get_columns("item_category", schema="inv")
    }

    if "minimum_stock" in columns:
        op.drop_column("item_category", "minimum_stock", schema="inv")
    if "reorder_point" in columns:
        op.drop_column("item_category", "reorder_point", schema="inv")
