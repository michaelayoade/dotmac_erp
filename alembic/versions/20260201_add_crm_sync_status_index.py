"""Add index on crm_status for CRM sync mapping

Revision ID: 20260201_crm_status_idx
Revises: 957c67719857
Create Date: 2026-02-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260201_crm_status_idx"
down_revision: Union[str, None] = "957c67719857"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Check if table exists
    if not inspector.has_table("crm_sync_mapping", schema="sync"):
        return

    # Check if index already exists (idempotent)
    indexes = {idx["name"] for idx in inspector.get_indexes("crm_sync_mapping", schema="sync")}
    if "idx_crm_sync_status" not in indexes:
        op.create_index(
            "idx_crm_sync_status",
            "crm_sync_mapping",
            ["organization_id", "crm_entity_type", "crm_status"],
            schema="sync",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("crm_sync_mapping", schema="sync"):
        indexes = {idx["name"] for idx in inspector.get_indexes("crm_sync_mapping", schema="sync")}
        if "idx_crm_sync_status" in indexes:
            op.drop_index("idx_crm_sync_status", table_name="crm_sync_mapping", schema="sync")
