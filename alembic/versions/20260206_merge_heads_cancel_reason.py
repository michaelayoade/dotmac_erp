"""Merge heads after adding material request cancel reason.

Revision ID: 20260206_merge_heads_cancel_reason
Revises: 20260206_add_inv_count_indexes, 20260206_add_material_request_cancel_reason, 2c732d9afaa6
Create Date: 2026-02-06
"""

revision = "20260206_merge_heads_cancel_reason"
down_revision = (
    "20260206_add_inv_count_indexes",
    "20260206_add_material_request_cancel_reason",
    "2c732d9afaa6",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Merge heads; no-op."""
    pass


def downgrade() -> None:
    """Downgrade merge; no-op."""
    pass
