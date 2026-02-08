"""Add cancel_reason to material request.

Revision ID: 20260206_add_material_request_cancel_reason
Revises: 20260206_add_item_category_reorder_levels
Create Date: 2026-02-06
"""

import sqlalchemy as sa

from alembic import op

revision = "20260206_add_material_request_cancel_reason"
down_revision = "20260206_add_item_category_reorder_levels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add cancel_reason column to inv.material_request."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("material_request", schema="inv"):
        return

    columns = {
        col["name"] for col in inspector.get_columns("material_request", schema="inv")
    }

    if "cancel_reason" not in columns:
        op.add_column(
            "material_request",
            sa.Column("cancel_reason", sa.Text, nullable=True),
            schema="inv",
        )


def downgrade() -> None:
    """Remove cancel_reason column from inv.material_request."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("material_request", schema="inv"):
        return

    columns = {
        col["name"] for col in inspector.get_columns("material_request", schema="inv")
    }

    if "cancel_reason" in columns:
        op.drop_column("material_request", "cancel_reason", schema="inv")
