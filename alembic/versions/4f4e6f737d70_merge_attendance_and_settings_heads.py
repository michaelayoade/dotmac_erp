"""merge attendance and settings heads

Revision ID: 4f4e6f737d70
Revises: add_attendance_requests_shift_assignments, add_settingdomain_values
Create Date: 2026-01-21 14:40:37.846376

"""

from alembic import op
import sqlalchemy as sa


revision = '4f4e6f737d70'
down_revision = ('add_attendance_requests_shift_assignments', 'add_settingdomain_values')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
