"""Add MATERIAL_REQUEST to sequence_type enum.

Revision ID: 20260203_add_material_request_sequence_type
Revises: 20260202_create_fleet_management_schema
Create Date: 2026-02-03
"""
import sqlalchemy as sa
from alembic import op

revision = "20260203_add_material_request_sequence_type"
down_revision = "20260202_create_fleet_management_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add MATERIAL_REQUEST value to sequence_type enum."""
    op.execute(
        "ALTER TYPE sequence_type ADD VALUE IF NOT EXISTS 'MATERIAL_REQUEST'"
    )


def downgrade() -> None:
    """PostgreSQL does not support removing enum values; no-op."""
    pass
