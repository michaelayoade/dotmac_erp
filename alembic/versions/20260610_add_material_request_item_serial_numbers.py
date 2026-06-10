"""add material request item serial numbers

Revision ID: 20260610_mri_serials
Revises: 20260610_mr_pending_stock
Create Date: 2026-06-10 14:20:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "20260610_mri_serials"
down_revision = "20260610_mr_pending_stock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "material_request_item",
        sa.Column("serial_numbers", sa.ARRAY(sa.Text()), nullable=True),
        schema="inv",
    )


def downgrade() -> None:
    op.drop_column("material_request_item", "serial_numbers", schema="inv")
