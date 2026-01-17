"""Add ITEM sequence type for numbering.

Revision ID: add_item_sequence_type
Revises: extend_alembic_version
Create Date: 2025-02-20
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "add_item_sequence_type"
down_revision = "extend_alembic_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE sequence_type ADD VALUE IF NOT EXISTS 'ITEM'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values safely.
    pass
