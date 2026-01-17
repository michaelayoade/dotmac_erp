"""merge heads

Revision ID: 5c7e3587f12a
Revises: add_flexible_tax_support, add_item_sequence_type, add_sync_tables
Create Date: 2026-01-16 13:41:35.330990

"""

from alembic import op
import sqlalchemy as sa


revision = '5c7e3587f12a'
down_revision = ('add_flexible_tax_support', 'add_item_sequence_type', 'add_sync_tables')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
