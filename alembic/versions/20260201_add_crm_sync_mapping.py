"""Add CRM sync mapping table for DotMac CRM integration

Revision ID: 20260201_crm_sync
Revises: 20260201_external_sync
Create Date: 2026-02-01

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260201_crm_sync"
down_revision: Union[str, None] = "20260201_external_sync"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Check if table already exists (idempotent)
    if inspector.has_table("crm_sync_mapping", schema="sync"):
        return

    # Check if sync schema exists, create if not
    existing_schemas = inspector.get_schema_names()
    if "sync" not in existing_schemas:
        op.execute("CREATE SCHEMA IF NOT EXISTS sync")

    # Create enums if they don't exist
    existing_enums = [e["name"] for e in inspector.get_enums(schema="sync")]

    if "crm_entity_type" not in existing_enums:
        crm_entity_type = postgresql.ENUM(
            "PROJECT",
            "TICKET",
            "WORK_ORDER",
            name="crm_entity_type",
            schema="sync",
        )
        crm_entity_type.create(bind)

    if "crm_sync_status" not in existing_enums:
        crm_sync_status = postgresql.ENUM(
            "ACTIVE",
            "COMPLETED",
            "CANCELLED",
            "ARCHIVED",
            name="crm_sync_status",
            schema="sync",
        )
        crm_sync_status.create(bind)

    # Create table
    op.create_table(
        "crm_sync_mapping",
        # Primary key
        sa.Column(
            "mapping_id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        # Organization (multi-tenancy)
        sa.Column("organization_id", sa.UUID(), nullable=False),
        # CRM source identification
        sa.Column(
            "crm_entity_type",
            postgresql.ENUM(
                "PROJECT",
                "TICKET",
                "WORK_ORDER",
                name="crm_entity_type",
                schema="sync",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "crm_id",
            sa.String(36),
            nullable=False,
            comment="UUID from DotMac CRM",
        ),
        # Local ERP entity reference
        sa.Column(
            "local_entity_type",
            sa.String(50),
            nullable=False,
            comment="Target table: 'project', 'ticket', 'task'",
        ),
        sa.Column(
            "local_entity_id",
            sa.UUID(),
            nullable=False,
            comment="UUID of the entity in ERP",
        ),
        # CRM status tracking
        sa.Column(
            "crm_status",
            postgresql.ENUM(
                "ACTIVE",
                "COMPLETED",
                "CANCELLED",
                "ARCHIVED",
                name="crm_sync_status",
                schema="sync",
                create_type=False,
            ),
            nullable=False,
            server_default="ACTIVE",
        ),
        # Cached display data
        sa.Column(
            "display_name",
            sa.String(255),
            nullable=False,
            comment="Name/subject/title from CRM for display",
        ),
        sa.Column(
            "display_code",
            sa.String(80),
            nullable=True,
            comment="Code/number from CRM",
        ),
        sa.Column(
            "customer_name",
            sa.String(200),
            nullable=True,
            comment="Customer name from CRM",
        ),
        # Full CRM data cache
        sa.Column(
            "crm_data",
            postgresql.JSON(),
            nullable=True,
            comment="Full payload from CRM for reference",
        ),
        # Sync tracking
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "crm_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Last update time in CRM",
        ),
        # Error tracking
        sa.Column("last_error", sa.Text(), nullable=True),
        # Audit
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        # Constraints
        sa.PrimaryKeyConstraint("mapping_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["core_org.organization.organization_id"],
            name="fk_crm_sync_organization",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "crm_entity_type",
            "crm_id",
            name="uq_crm_sync_org_type_id",
        ),
        schema="sync",
    )

    # Create indexes
    op.create_index(
        "idx_crm_sync_org",
        "crm_sync_mapping",
        ["organization_id"],
        schema="sync",
    )
    op.create_index(
        "idx_crm_sync_crm_id",
        "crm_sync_mapping",
        ["crm_id"],
        schema="sync",
    )
    op.create_index(
        "idx_crm_sync_local",
        "crm_sync_mapping",
        ["local_entity_type", "local_entity_id"],
        schema="sync",
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("crm_sync_mapping", schema="sync"):
        # Drop indexes first
        op.drop_index(
            "idx_crm_sync_local", table_name="crm_sync_mapping", schema="sync"
        )
        op.drop_index(
            "idx_crm_sync_crm_id", table_name="crm_sync_mapping", schema="sync"
        )
        op.drop_index("idx_crm_sync_org", table_name="crm_sync_mapping", schema="sync")
        # Drop table
        op.drop_table("crm_sync_mapping", schema="sync")

    # Optionally drop enums (might be used elsewhere)
    # op.execute("DROP TYPE IF EXISTS sync.crm_entity_type")
    # op.execute("DROP TYPE IF EXISTS sync.crm_sync_status")
