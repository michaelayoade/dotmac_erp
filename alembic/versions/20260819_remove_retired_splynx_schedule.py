"""Remove retired Splynx scheduled task.

Revision ID: 20260819_remove_retired_splynx_schedule
Revises: 20260814_database_roles
Create Date: 2026-08-19
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260819_remove_retired_splynx_schedule"
down_revision = "20260814_database_roles"
branch_labels = None
depends_on = None

RETIRED_TASK_NAME = "app.tasks.splynx.run_scheduled_splynx_sync"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("scheduled_tasks"):
        return

    op.execute(
        sa.text("DELETE FROM scheduled_tasks WHERE task_name = :task_name").bindparams(
            task_name=RETIRED_TASK_NAME
        )
    )


def downgrade() -> None:
    # Do not recreate a retired external integration task.
    pass
