"""Add DOTMAC_CRM to integration_type enum.

Revision ID: add_dotmac_crm_enum
Revises:
Create Date: 2026-02-01

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "add_dotmac_crm_enum"
down_revision = "add_integration_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add DOTMAC_CRM to integration_type enum
    # Using raw SQL since Alembic doesn't handle enum additions well
    op.execute("ALTER TYPE integration_type ADD VALUE IF NOT EXISTS 'DOTMAC_CRM'")


def downgrade() -> None:
    # PostgreSQL doesn't support removing enum values
    # This would require recreating the type
    pass
