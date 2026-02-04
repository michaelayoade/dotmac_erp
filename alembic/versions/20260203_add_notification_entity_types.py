"""Add missing entity types to notification enum.

Revision ID: 20260203_add_notification_entity_types
Revises: 20260203_add_material_request_sequence_type
Create Date: 2026-02-03
"""

from alembic import op

revision = "20260203_add_notification_entity_types"
down_revision = "20260203_add_material_request_sequence_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add missing values to entitytype enum used by public.notification."""
    op.execute("ALTER TYPE entitytype ADD VALUE IF NOT EXISTS 'FISCAL_PERIOD'")
    op.execute("ALTER TYPE entitytype ADD VALUE IF NOT EXISTS 'TAX_PERIOD'")
    op.execute("ALTER TYPE entitytype ADD VALUE IF NOT EXISTS 'BANK_RECONCILIATION'")
    op.execute("ALTER TYPE entitytype ADD VALUE IF NOT EXISTS 'INVOICE'")
    op.execute("ALTER TYPE entitytype ADD VALUE IF NOT EXISTS 'SUBLEDGER'")
    op.execute("ALTER TYPE entitytype ADD VALUE IF NOT EXISTS 'DISCIPLINE'")


def downgrade() -> None:
    """PostgreSQL does not support removing enum values; no-op."""
    pass
