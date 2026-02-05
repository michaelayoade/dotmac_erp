"""Add organization scope to domain_settings.

Enables per-organization setting overrides while maintaining global defaults.

Resolution chain: org-specific → global → code default

Revision ID: 20260130_add_settings_org_scope
Revises: 20260130_add_payroll_proration
Create Date: 2026-01-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260130_add_settings_org_scope"
down_revision = "20260130_add_payroll_proration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the setting_scope enum type
    setting_scope_enum = postgresql.ENUM(
        "GLOBAL", "ORG_SPECIFIC",
        name="setting_scope",
    )
    setting_scope_enum.create(op.get_bind(), checkfirst=True)

    # Add organization_id column (nullable - NULL means global)
    op.add_column(
        "domain_settings",
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "core_org.organization.organization_id",
                ondelete="CASCADE",
            ),
            nullable=True,
            comment="NULL = global setting, UUID = org-specific setting",
        ),
    )

    # Add scope column with default GLOBAL
    op.add_column(
        "domain_settings",
        sa.Column(
            "scope",
            setting_scope_enum,
            nullable=False,
            server_default="GLOBAL",
            comment="GLOBAL for shared settings, ORG_SPECIFIC for per-org overrides",
        ),
    )

    # Add index on organization_id
    op.create_index(
        "ix_domain_settings_org",
        "domain_settings",
        ["organization_id"],
    )

    # Drop old unique constraint
    op.drop_constraint(
        "uq_domain_settings_domain_key",
        "domain_settings",
        type_="unique",
    )

    # Create new unique constraint including organization_id
    op.create_unique_constraint(
        "uq_domain_settings_domain_key_org",
        "domain_settings",
        ["domain", "key", "organization_id"],
    )

    # Enforce single global setting per (domain, key)
    op.create_index(
        "uq_domain_settings_domain_key_global",
        "domain_settings",
        ["domain", "key"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL"),
    )

    # Add organization_id to history table for tracking
    op.add_column(
        "domain_setting_history",
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Organization ID (NULL = global setting)",
        ),
    )


def downgrade() -> None:
    # Remove organization_id from history table
    op.drop_column("domain_setting_history", "organization_id")

    # Drop new unique constraint
    op.drop_index("uq_domain_settings_domain_key_global", table_name="domain_settings")
    op.drop_constraint(
        "uq_domain_settings_domain_key_org",
        "domain_settings",
        type_="unique",
    )

    # Recreate old unique constraint
    op.create_unique_constraint(
        "uq_domain_settings_domain_key",
        "domain_settings",
        ["domain", "key"],
    )

    # Drop index
    op.drop_index("ix_domain_settings_org", "domain_settings")

    # Remove columns
    op.drop_column("domain_settings", "scope")
    op.drop_column("domain_settings", "organization_id")

    # Drop enum type
    setting_scope_enum = postgresql.ENUM("GLOBAL", "ORG_SPECIFIC", name="setting_scope")
    setting_scope_enum.drop(op.get_bind(), checkfirst=True)
