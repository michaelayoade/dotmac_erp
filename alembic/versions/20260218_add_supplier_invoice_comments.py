"""Add comments column to ap.supplier_invoice.

Revision ID: 20260218_add_supplier_invoice_comments
Revises: 20260218_add_rotating_pattern_work_days
Create Date: 2026-02-18
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260218_add_supplier_invoice_comments"
down_revision = "20260218_add_rotating_pattern_work_days"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "supplier_invoice", sa.Column("comments", sa.Text(), nullable=True), schema="ap"
    )


def downgrade() -> None:
    op.drop_column("supplier_invoice", "comments", schema="ap")
