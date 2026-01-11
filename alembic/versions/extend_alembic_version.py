"""Widen alembic version column length.

Revision ID: extend_alembic_version
Revises: add_automation_schema
Create Date: 2025-02-15
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "extend_alembic_version"
down_revision = "add_automation_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("alembic_version", schema="public"):
        return

    op.execute(
        "ALTER TABLE public.alembic_version "
        "ALTER COLUMN version_num TYPE VARCHAR(64)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("alembic_version", schema="public"):
        return

    op.execute(
        "ALTER TABLE public.alembic_version "
        "ALTER COLUMN version_num TYPE VARCHAR(32)"
    )
