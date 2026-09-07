"""Add workflow assignment/custom-field actions and assignment storage.

Revision ID: 20260909_automation_assignments
Revises: 20260908_custom_field_values
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260909_automation_assignments"
down_revision: str | None = "20260908_custom_field_values"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE workflow_action_type ADD VALUE IF NOT EXISTS 'ASSIGN'")
    op.execute(
        "ALTER TYPE workflow_action_type ADD VALUE IF NOT EXISTS 'UPDATE_CUSTOM_FIELD'"
    )
    op.alter_column(
        "custom_field_value", "created_by", schema="automation", nullable=True
    )
    op.create_table(
        "entity_assignment",
        sa.Column(
            "assignment_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "entity_type",
            postgresql.ENUM(name="workflow_entity_type", create_type=False),
            nullable=False,
        ),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assignee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy", sa.String(30), nullable=False, server_default="DIRECT"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(["assignee_id"], ["people.id"], ondelete="RESTRICT"),
        schema="automation",
    )
    op.create_index(
        "idx_entity_assignment_current",
        "entity_assignment",
        ["organization_id", "entity_type", "entity_id", "is_active"],
        schema="automation",
    )
    op.create_index(
        "idx_entity_assignment_person",
        "entity_assignment",
        ["organization_id", "assignee_id"],
        schema="automation",
    )


def downgrade() -> None:
    op.drop_table("entity_assignment", schema="automation")
    op.alter_column(
        "custom_field_value", "created_by", schema="automation", nullable=False
    )
    # PostgreSQL enum values are intentionally additive and are not removed.
