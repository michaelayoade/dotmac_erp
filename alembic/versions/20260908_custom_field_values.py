"""Add tenant-scoped custom field value storage.

Revision ID: 20260908_custom_field_values
Revises: 20260907_automation_rbac
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260908_custom_field_values"
down_revision: str | None = "20260907_automation_rbac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "custom_field_value",
        sa.Column(
            "value_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "entity_type",
            postgresql.ENUM(name="custom_field_entity_type", create_type=False),
            nullable=False,
        ),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(
            ["field_id"],
            ["automation.custom_field_definition.field_id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "field_id",
            "entity_id",
            name="uq_custom_field_value_entity",
        ),
        schema="automation",
    )
    op.create_index(
        "idx_custom_field_value_entity",
        "custom_field_value",
        ["organization_id", "entity_type", "entity_id"],
        schema="automation",
    )
    op.create_index(
        "idx_custom_field_value_field",
        "custom_field_value",
        ["organization_id", "field_id"],
        schema="automation",
    )
    op.execute("ALTER TABLE automation.custom_field_value ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE automation.custom_field_value FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY custom_field_value_tenant_isolation
        ON automation.custom_field_value
        USING (
            organization_id = NULLIF(
                current_setting('app.current_organization_id', true), ''
            )::uuid
        )
        WITH CHECK (
            organization_id = NULLIF(
                current_setting('app.current_organization_id', true), ''
            )::uuid
        )
        """
    )


def downgrade() -> None:
    op.drop_table("custom_field_value", schema="automation")
