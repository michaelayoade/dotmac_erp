"""Add external_sync table for tracking Splynx sync

Revision ID: 20260201_external_sync
Revises: 20260201_merge_heads_for_remita
Create Date: 2026-02-01

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260201_external_sync"
down_revision: Union[str, None] = "20260201_merge_heads_for_remita"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Check if table already exists
    if inspector.has_table("external_sync", schema="ar"):
        return

    # Create enums if they don't exist
    existing_enums = [e["name"] for e in inspector.get_enums(schema="ar")]

    if "external_source" not in existing_enums:
        external_source = postgresql.ENUM(
            "SPLYNX",
            "ERPNEXT",
            "CRM",
            name="external_source",
            schema="ar",
        )
        external_source.create(bind)

    if "sync_entity_type" not in existing_enums:
        sync_entity_type = postgresql.ENUM(
            "CUSTOMER",
            "INVOICE",
            "PAYMENT",
            "CREDIT_NOTE",
            name="sync_entity_type",
            schema="ar",
        )
        sync_entity_type.create(bind)

    # Create table
    op.create_table(
        "external_sync",
        sa.Column(
            "sync_id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column(
            "source",
            postgresql.ENUM(
                "SPLYNX",
                "ERPNEXT",
                "CRM",
                name="external_source",
                schema="ar",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "entity_type",
            postgresql.ENUM(
                "CUSTOMER",
                "INVOICE",
                "PAYMENT",
                "CREDIT_NOTE",
                name="sync_entity_type",
                schema="ar",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "external_id",
            sa.String(100),
            nullable=False,
            comment="ID in the external system",
        ),
        sa.Column(
            "local_entity_id",
            sa.UUID(),
            nullable=False,
            comment="UUID of the entity in ERP",
        ),
        sa.Column(
            "external_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Last update time in external system",
        ),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="When this record was last synced",
        ),
        sa.Column(
            "sync_hash",
            sa.String(64),
            nullable=True,
            comment="Hash of synced data for change detection",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("sync_id"),
        sa.UniqueConstraint(
            "organization_id",
            "source",
            "entity_type",
            "external_id",
            name="uq_external_sync_source_entity",
        ),
        schema="ar",
    )

    # Create indexes
    op.create_index(
        "idx_external_sync_lookup",
        "external_sync",
        ["organization_id", "source", "entity_type", "external_id"],
        schema="ar",
    )
    op.create_index(
        "idx_external_sync_local",
        "external_sync",
        ["organization_id", "local_entity_id"],
        schema="ar",
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("external_sync", schema="ar"):
        op.drop_index(
            "idx_external_sync_local", table_name="external_sync", schema="ar"
        )
        op.drop_index(
            "idx_external_sync_lookup", table_name="external_sync", schema="ar"
        )
        op.drop_table("external_sync", schema="ar")

    # Drop enums (optional, might be used elsewhere)
    # op.execute("DROP TYPE IF EXISTS ar.external_source")
    # op.execute("DROP TYPE IF EXISTS ar.sync_entity_type")
