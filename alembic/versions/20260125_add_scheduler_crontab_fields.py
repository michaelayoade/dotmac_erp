"""Add crontab fields to scheduled_tasks

Revision ID: add_scheduler_crontab
Revises:
Create Date: 2026-01-25

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_scheduler_crontab'
down_revision = "799a0ecebdd4"  # Fixed: connect to initial schema
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add crontab schedule type to enum
    op.execute("ALTER TYPE scheduletype ADD VALUE IF NOT EXISTS 'crontab'")

    # Add crontab fields to scheduled_tasks
    op.add_column(
        'scheduled_tasks',
        sa.Column('cron_minute', sa.String(20), nullable=True, server_default='0')
    )
    op.add_column(
        'scheduled_tasks',
        sa.Column('cron_hour', sa.String(20), nullable=True, server_default='8')
    )
    op.add_column(
        'scheduled_tasks',
        sa.Column('cron_day_of_week', sa.String(20), nullable=True, server_default='*')
    )
    op.add_column(
        'scheduled_tasks',
        sa.Column('cron_day_of_month', sa.String(20), nullable=True, server_default='*')
    )
    op.add_column(
        'scheduled_tasks',
        sa.Column('cron_month_of_year', sa.String(20), nullable=True, server_default='*')
    )


def downgrade() -> None:
    op.drop_column('scheduled_tasks', 'cron_month_of_year')
    op.drop_column('scheduled_tasks', 'cron_day_of_month')
    op.drop_column('scheduled_tasks', 'cron_day_of_week')
    op.drop_column('scheduled_tasks', 'cron_hour')
    op.drop_column('scheduled_tasks', 'cron_minute')
    # Note: Cannot remove enum value in PostgreSQL easily, leaving 'crontab' in enum
