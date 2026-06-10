"""Add pending stock material request status.

Revision ID: 20260610_mr_pending_stock
Revises: 20260609_ap_store_receipt_approval
Create Date: 2026-06-10
"""

from __future__ import annotations

from alembic import op


revision = "20260610_mr_pending_stock"
down_revision = "20260609_ap_store_receipt_approval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE inv.material_request_status ADD VALUE IF NOT EXISTS 'PENDING_STOCK'"
    )


def downgrade() -> None:
    # PostgreSQL cannot remove enum values safely without rebuilding the enum.
    pass
