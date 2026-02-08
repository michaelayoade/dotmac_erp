"""Add organization branding table for per-org theming.

Revision ID: add_organization_branding
Revises: create_expense_tables
Create Date: 2025-01-21

This migration adds the organization_branding table to the core_org schema,
enabling multi-tenant branding with custom colors, logos, and typography.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.alembic_utils import ensure_enum

# revision identifiers, used by Alembic.
revision: str = "add_organization_branding"
down_revision: Union[str, None] = "create_expense_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    ensure_enum(
        bind, "border_radius_style", "sharp", "rounded", "pill", schema="core_org"
    )
    ensure_enum(bind, "button_style", "solid", "gradient", "outline", schema="core_org")
    ensure_enum(bind, "sidebar_style", "dark", "light", "brand", schema="core_org")

    # Create organization_branding table
    op.create_table(
        "organization_branding",
        # Primary key
        sa.Column(
            "branding_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # Foreign key to organization
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("core_org.organization.organization_id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        # Identity
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("tagline", sa.String(500), nullable=True),
        sa.Column("logo_url", sa.String(500), nullable=True),
        sa.Column("logo_dark_url", sa.String(500), nullable=True),
        sa.Column("favicon_url", sa.String(500), nullable=True),
        sa.Column("brand_mark", sa.String(4), nullable=True),
        # Primary color palette
        sa.Column("primary_color", sa.String(7), nullable=True),  # Hex format #RRGGBB
        sa.Column("primary_light", sa.String(7), nullable=True),
        sa.Column("primary_dark", sa.String(7), nullable=True),
        # Accent color palette
        sa.Column("accent_color", sa.String(7), nullable=True),
        sa.Column("accent_light", sa.String(7), nullable=True),
        sa.Column("accent_dark", sa.String(7), nullable=True),
        # Extended palette overrides (optional)
        sa.Column("success_color", sa.String(7), nullable=True),
        sa.Column("warning_color", sa.String(7), nullable=True),
        sa.Column("danger_color", sa.String(7), nullable=True),
        # Typography
        sa.Column("font_family_display", sa.String(100), nullable=True),
        sa.Column("font_family_body", sa.String(100), nullable=True),
        sa.Column("font_family_mono", sa.String(100), nullable=True),
        # UI Preferences
        sa.Column(
            "border_radius",
            postgresql.ENUM(
                "sharp",
                "rounded",
                "pill",
                name="border_radius_style",
                schema="core_org",
                create_type=False,
            ),
            nullable=True,
            server_default="rounded",
        ),
        sa.Column(
            "button_style",
            postgresql.ENUM(
                "solid",
                "gradient",
                "outline",
                name="button_style",
                schema="core_org",
                create_type=False,
            ),
            nullable=True,
            server_default="gradient",
        ),
        sa.Column(
            "sidebar_style",
            postgresql.ENUM(
                "dark",
                "light",
                "brand",
                name="sidebar_style",
                schema="core_org",
                create_type=False,
            ),
            nullable=True,
            server_default="dark",
        ),
        # Custom CSS injection
        sa.Column("custom_css", sa.Text, nullable=True),
        # Status and timestamps
        sa.Column(
            "is_active", sa.Boolean, nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            onupdate=sa.func.now(),
        ),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "people.id", ondelete="SET NULL", name="fk_org_branding_created_by"
            ),
            nullable=True,
        ),
        schema="core_org",
    )

    # Create index on organization_id for faster lookups
    op.create_index(
        "ix_organization_branding_org_id",
        "organization_branding",
        ["organization_id"],
        schema="core_org",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_organization_branding_org_id",
        table_name="organization_branding",
        schema="core_org",
    )
    op.drop_table("organization_branding", schema="core_org")
    op.execute("DROP TYPE IF EXISTS core_org.sidebar_style")
    op.execute("DROP TYPE IF EXISTS core_org.button_style")
    op.execute("DROP TYPE IF EXISTS core_org.border_radius_style")
