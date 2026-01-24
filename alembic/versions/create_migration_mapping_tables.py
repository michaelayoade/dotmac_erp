"""Create migration mapping tables for People data migration.

Revision ID: create_migration_mapping_tables
Revises: add_hr_settings_to_org
Create Date: 2025-01-20

This migration creates temporary mapping tables in the migration schema
to support data migration from DotMac People to DotMac ERP.

Tables:
- company_org_map: Maps People company strings to Books organization UUIDs
- id_mapping: Maps old integer IDs to new UUIDs for all migrated entities
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "create_migration_mapping_tables"
down_revision = "add_hr_settings_to_org"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create company_org_map table
    op.create_table(
        "company_org_map",
        sa.Column(
            "company_name",
            sa.String(255),
            primary_key=True,
            comment="Company name from DotMac People",
        ),
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("core_org.organization.organization_id"),
            nullable=False,
            comment="Mapped organization ID in DotMac ERP",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema="migration",
        comment="Maps People company names to Books organization UUIDs",
    )

    # Create id_mapping table
    op.create_table(
        "id_mapping",
        sa.Column(
            "source_table",
            sa.String(100),
            nullable=False,
            comment="Source table name from DotMac People",
        ),
        sa.Column(
            "old_id",
            sa.String(100),
            nullable=False,
            comment="Original ID (may be int or string)",
        ),
        sa.Column(
            "new_id",
            UUID(as_uuid=True),
            nullable=False,
            comment="New UUID in DotMac ERP",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("source_table", "old_id"),
        schema="migration",
        comment="Maps old People IDs to new Books UUIDs for all migrated entities",
    )

    # Create index on new_id for reverse lookups
    op.create_index(
        "ix_migration_id_mapping_new_id",
        "id_mapping",
        ["new_id"],
        schema="migration",
    )


def downgrade() -> None:
    op.drop_index("ix_migration_id_mapping_new_id", schema="migration")
    op.drop_table("id_mapping", schema="migration")
    op.drop_table("company_org_map", schema="migration")
