"""Add scorecard perspective to department performance templates.

Revision ID: 20260707_dept_template_perspective
Revises: 20260707_dept_perf_templates
Create Date: 2026-07-07
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260707_dept_template_perspective"
down_revision: Union[str, None] = "20260707_dept_perf_templates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "department_performance_template",
        sa.Column(
            "scorecard_perspective",
            sa.String(length=20),
            nullable=False,
            server_default="PROCESS",
        ),
        schema="perf",
    )
    op.alter_column(
        "department_performance_template",
        "scorecard_perspective",
        server_default=None,
        schema="perf",
    )


def downgrade() -> None:
    op.drop_column(
        "department_performance_template",
        "scorecard_perspective",
        schema="perf",
    )
