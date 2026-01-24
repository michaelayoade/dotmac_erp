"""Add sequence types for support tickets, projects, and tasks.

Revision ID: 20260124_sequence_types_ops
Revises: 20260124_notification
Create Date: 2026-01-24
"""
from alembic import op

revision = "20260124_sequence_types_ops"
down_revision = "20260124_notification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE sequence_type ADD VALUE IF NOT EXISTS 'SUPPORT_TICKET'")
    op.execute("ALTER TYPE sequence_type ADD VALUE IF NOT EXISTS 'PROJECT'")
    op.execute("ALTER TYPE sequence_type ADD VALUE IF NOT EXISTS 'TASK'")


def downgrade() -> None:
    # Enum value removal is not supported without recreating the type.
    pass
