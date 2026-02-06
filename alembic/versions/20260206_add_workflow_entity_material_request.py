"""Add material request to workflow entity types.

Revision ID: 20260206_add_workflow_entity_material_request
Revises: 20260203_add_workflow_rule_versioning
Create Date: 2026-02-06
"""

from alembic import op

revision = "20260206_add_workflow_entity_material_request"
down_revision = "20260203_add_workflow_rule_versioning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Extend workflow_entity_type enum with material request."""
    op.execute(
        "ALTER TYPE workflow_entity_type ADD VALUE IF NOT EXISTS 'MATERIAL_REQUEST'"
    )


def downgrade() -> None:
    """PostgreSQL does not support removing enum values; no-op."""
    pass
