"""Add integration_config table.

Revision ID: add_integration_config
Revises: add_expense_project_ticket_fk
Create Date: 2026-01-23

This migration adds:
- sync.integration_config table for per-organization external system credentials
"""

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "add_integration_config"
down_revision: Union[str, None] = "add_expense_project_ticket_fk"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enum type for integration types (if not exists)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'integration_type') THEN
                CREATE TYPE integration_type AS ENUM (
                    'ERPNEXT',
                    'QUICKBOOKS',
                    'XERO',
                    'SAGE'
                );
            END IF;
        END$$;
    """)

    # Create integration_config table using raw SQL to avoid enum recreation
    op.execute("""
        CREATE TABLE sync.integration_config (
            config_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            integration_type integration_type NOT NULL,
            base_url VARCHAR(500) NOT NULL,
            api_key TEXT,
            api_secret TEXT,
            company VARCHAR(255),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            last_verified_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE,
            created_by_user_id UUID
        );

        COMMENT ON COLUMN sync.integration_config.api_key IS 'API key - should be encrypted at rest';
        COMMENT ON COLUMN sync.integration_config.api_secret IS 'API secret - should be encrypted at rest';
        COMMENT ON COLUMN sync.integration_config.company IS 'Company/tenant identifier in the external system';
        COMMENT ON COLUMN sync.integration_config.last_verified_at IS 'Last successful connection verification';
    """)

    # Create indexes
    op.execute("""
        CREATE INDEX idx_integration_config_org ON sync.integration_config(organization_id);
        CREATE INDEX idx_integration_config_type ON sync.integration_config(integration_type);
        CREATE UNIQUE INDEX idx_integration_config_unique_active
            ON sync.integration_config(organization_id, integration_type)
            WHERE is_active = true;
    """)


def downgrade() -> None:
    # Drop indexes
    op.drop_index(
        "idx_integration_config_unique_active",
        table_name="integration_config",
        schema="sync",
    )
    op.drop_index(
        "idx_integration_config_type",
        table_name="integration_config",
        schema="sync",
    )
    op.drop_index(
        "idx_integration_config_org",
        table_name="integration_config",
        schema="sync",
    )

    # Drop table
    op.drop_table("integration_config", schema="sync")

    # Drop enum
    op.execute("DROP TYPE integration_type;")
