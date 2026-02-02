"""merge external_sync

Revision ID: cb10d9ec8d2e
Revises: 20260201_external_sync, 20260201_recipient_name
Create Date: 2026-02-01 17:40:08.404310

"""

from alembic import op
import sqlalchemy as sa


revision = 'cb10d9ec8d2e'
down_revision = ('20260201_external_sync', '20260201_recipient_name')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
