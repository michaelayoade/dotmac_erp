"""merge_heads_for_discipline

Revision ID: 5709758f2538
Revises: 20260128_add_applicant_verification, 20260128_payroll_gl
Create Date: 2026-01-28 02:26:59.189934

"""

from alembic import op
import sqlalchemy as sa


revision = '5709758f2538'
down_revision = ('20260128_add_applicant_verification', '20260128_payroll_gl')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
