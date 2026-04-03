"""Add PMS-specific config JSON field to perf.appraisal_template.

Revision ID: 20260403_add_appraisal_template_pms_config
Revises: 20260403_add_appraisal_template_profile
Create Date: 2026-04-03
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260403_add_appraisal_template_pms_config"
down_revision = "20260403_add_appraisal_template_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("appraisal_template", schema="perf"):
        return

    columns = {
        column["name"]
        for column in inspector.get_columns("appraisal_template", schema="perf")
    }
    if "pms_config" not in columns:
        op.add_column(
            "appraisal_template",
            sa.Column("pms_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            schema="perf",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("appraisal_template", schema="perf"):
        return

    columns = {
        column["name"]
        for column in inspector.get_columns("appraisal_template", schema="perf")
    }
    if "pms_config" in columns:
        op.drop_column("appraisal_template", "pms_config", schema="perf")
