"""Add BANK_STATEMENT to SequenceType enum.

Revision ID: 20260212_add_bank_statement_sequence_type
Revises: 20260212_add_erpnext_id_to_synced_models
Create Date: 2026-02-12
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260212_add_bank_statement_sequence_type"
down_revision = "20260212_add_erpnext_id_to_synced_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add BANK_STATEMENT value to the sequence_type enum (idempotent)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_enum
                WHERE enumlabel = 'BANK_STATEMENT'
                AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'sequence_type')
            ) THEN
                ALTER TYPE sequence_type ADD VALUE 'BANK_STATEMENT';
            END IF;
        END $$;
    """)


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; no-op.
    pass
