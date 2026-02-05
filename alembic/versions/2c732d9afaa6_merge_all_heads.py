"""merge_all_heads

Revision ID: 2c732d9afaa6
Revises: 20260128_add_organization_slug, 20260204_add_material_request_ticket_id, ae7bbaefd73d
Create Date: 2026-02-05 11:32:07.195090

"""

from alembic import op
import sqlalchemy as sa


revision = '2c732d9afaa6'
down_revision = ('20260128_add_organization_slug', '20260204_add_material_request_ticket_id', 'ae7bbaefd73d')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
