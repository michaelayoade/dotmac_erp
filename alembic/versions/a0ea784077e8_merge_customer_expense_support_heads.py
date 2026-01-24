"""merge_customer_expense_support_heads

Revision ID: a0ea784077e8
Revises: 20260124_add_customer_relationships, 20260124_expense_gl, 20260124_support
Create Date: 2026-01-24 10:13:18.060815

"""

from alembic import op
import sqlalchemy as sa


revision = 'a0ea784077e8'
down_revision = ('20260124_add_customer_relationships', '20260124_expense_gl', '20260124_support')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
