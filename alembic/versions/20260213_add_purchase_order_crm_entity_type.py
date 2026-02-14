"""Add PURCHASE_ORDER value to CRM entity type enum.

Revision ID: 20260213_add_purchase_order_crm_entity_type
Revises: 20260213_add_audit_event_actor_links
Create Date: 2026-02-13

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260213_add_purchase_order_crm_entity_type"
down_revision = "20260213_add_audit_event_actor_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE sync.crm_entity_type ADD VALUE IF NOT EXISTS 'PURCHASE_ORDER'"
    )


def downgrade() -> None:
    # PostgreSQL does not support removing enum values.
    # The value will remain but be unused after downgrade.
    pass
