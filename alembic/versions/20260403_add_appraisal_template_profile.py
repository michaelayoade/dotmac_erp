"""Add template profile to perf.appraisal_template for mode-aware template usage.

Revision ID: 20260403_add_appraisal_template_profile
Revises: 20260402_add_org_performance_mode
Create Date: 2026-04-03
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260403_add_appraisal_template_profile"
down_revision = "20260402_add_org_performance_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_enums = {enum["name"] for enum in inspector.get_enums()}
    if "appraisal_template_profile" not in existing_enums:
        postgresql.ENUM(
            "PRIVATE",
            "PMS",
            "BOTH",
            name="appraisal_template_profile",
        ).create(bind, checkfirst=True)

    if not inspector.has_table("appraisal_template", schema="perf"):
        return

    columns = {
        column["name"]
        for column in inspector.get_columns("appraisal_template", schema="perf")
    }
    if "template_profile" not in columns:
        op.add_column(
            "appraisal_template",
            sa.Column(
                "template_profile",
                postgresql.ENUM(
                    "PRIVATE",
                    "PMS",
                    "BOTH",
                    name="appraisal_template_profile",
                    create_type=False,
                ),
                nullable=False,
                server_default=sa.text("'BOTH'"),
            ),
            schema="perf",
        )

    indexes = {
        index["name"]
        for index in inspector.get_indexes("appraisal_template", schema="perf")
    }
    if "idx_appraisal_template_profile" not in indexes:
        op.create_index(
            "idx_appraisal_template_profile",
            "appraisal_template",
            ["organization_id", "template_profile"],
            schema="perf",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("appraisal_template", schema="perf"):
        indexes = {
            index["name"]
            for index in inspector.get_indexes("appraisal_template", schema="perf")
        }
        if "idx_appraisal_template_profile" in indexes:
            op.drop_index(
                "idx_appraisal_template_profile",
                table_name="appraisal_template",
                schema="perf",
            )

        columns = {
            column["name"]
            for column in inspector.get_columns("appraisal_template", schema="perf")
        }
        if "template_profile" in columns:
            op.drop_column("appraisal_template", "template_profile", schema="perf")

    postgresql.ENUM(
        "PRIVATE",
        "PMS",
        "BOTH",
        name="appraisal_template_profile",
    ).drop(bind, checkfirst=True)

