"""Add project templates and link projects to templates.

Revision ID: 20260125_project_templates
Revises: 20260124_task_asset_fks, 20260124_transfer_batch, 20260125_add_hr_checklist_jobdesc_erpnext_fields
Create Date: 2026-01-25
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260125_project_templates"
down_revision: Union[str, Sequence[str], None] = (
    "20260124_task_asset_fks",
    "20260124_transfer_batch",
    "20260125_add_hr_checklist_jobdesc_erpnext_fields",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_template",
        sa.Column(
            "template_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("core_org.organization.organization_id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "project_type",
            postgresql.ENUM(
                "INTERNAL",
                "CLIENT",
                "FIXED_PRICE",
                "TIME_MATERIAL",
                name="project_type",
                schema="pm",
                create_type=False,
            ),
            nullable=False,
            server_default="INTERNAL",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.UniqueConstraint("organization_id", "name", name="uq_project_template_name"),
        schema="pm",
    )
    op.create_index(
        "ix_project_template_org",
        "project_template",
        ["organization_id"],
        schema="pm",
    )

    op.create_table(
        "project_template_task",
        sa.Column(
            "template_task_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "template_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pm.project_template.template_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "template_id",
            "order_index",
            name="uq_project_template_task_order",
        ),
        schema="pm",
    )
    op.create_index(
        "ix_project_template_task_template",
        "project_template_task",
        ["template_id"],
        schema="pm",
    )

    op.create_table(
        "project_template_task_dependency",
        sa.Column(
            "dependency_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "template_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "pm.project_template_task.template_task_id", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column(
            "depends_on_template_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "pm.project_template_task.template_task_id", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column(
            "dependency_type",
            postgresql.ENUM(
                "FINISH_TO_START",
                "START_TO_START",
                "FINISH_TO_FINISH",
                "START_TO_FINISH",
                name="dependency_type",
                schema="pm",
                create_type=False,
            ),
            nullable=False,
            server_default="FINISH_TO_START",
        ),
        sa.Column("lag_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "template_task_id",
            "depends_on_template_task_id",
            name="uq_project_template_task_dependency",
        ),
        sa.CheckConstraint(
            "template_task_id != depends_on_template_task_id",
            name="chk_project_template_task_dependency_self",
        ),
        schema="pm",
    )
    op.create_index(
        "ix_project_template_task_dep_task",
        "project_template_task_dependency",
        ["template_task_id"],
        schema="pm",
    )

    op.add_column(
        "project",
        sa.Column(
            "project_template_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pm.project_template.template_id"),
            nullable=True,
        ),
        schema="core_org",
    )
    op.create_index(
        "ix_project_template_id",
        "project",
        ["project_template_id"],
        schema="core_org",
    )


def downgrade() -> None:
    op.drop_index("ix_project_template_id", table_name="project", schema="core_org")
    op.drop_column("project", "project_template_id", schema="core_org")

    op.drop_index(
        "ix_project_template_task_dep_task",
        table_name="project_template_task_dependency",
        schema="pm",
    )
    op.drop_table("project_template_task_dependency", schema="pm")
    op.drop_index(
        "ix_project_template_task_template",
        table_name="project_template_task",
        schema="pm",
    )
    op.drop_table("project_template_task", schema="pm")
    op.drop_index("ix_project_template_org", table_name="project_template", schema="pm")
    op.drop_table("project_template", schema="pm")
