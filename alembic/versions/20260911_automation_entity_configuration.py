"""Add tenant-configurable automation entity availability.

Revision ID: 20260911_automation_entity_configuration
Revises: 20260910_automation_module_connectors
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260911_automation_entity_configuration"
down_revision: str | None = "20260910_automation_module_connectors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "entity_configuration",
        sa.Column(
            "configuration_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["core_org.organization.organization_id"],
        ),
        sa.UniqueConstraint(
            "organization_id",
            "entity_type",
            name="uq_automation_entity_configuration",
        ),
        schema="automation",
    )
    op.create_index(
        "idx_automation_entity_configuration_org",
        "entity_configuration",
        ["organization_id", "is_enabled"],
        schema="automation",
    )
    op.execute("ALTER TABLE automation.entity_configuration ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE automation.entity_configuration FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY entity_configuration_tenant_isolation
        ON automation.entity_configuration
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
    op.drop_table("entity_configuration", schema="automation")
