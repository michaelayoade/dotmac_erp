"""Merge heads before adding Remita integration

Revision ID: 20260201_merge_heads_for_remita
Revises: 20260131_add_unique_salary_slip_number, d98bd2539ace
Create Date: 2026-02-01

"""
from alembic import op
import sqlalchemy as sa

revision = "20260201_merge_heads_for_remita"
down_revision = ("20260131_add_unique_salary_slip_number", "d98bd2539ace")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
