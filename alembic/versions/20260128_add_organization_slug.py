"""Add organization slug for public URLs.

Revision ID: 20260128_add_organization_slug
Revises:
Create Date: 2026-01-28

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260128_add_organization_slug"
down_revision = "create_ifrs_schemas"  # Fixed: connect to initial schema
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("organization", schema="core_org")}
    indexes = {idx["name"] for idx in inspector.get_indexes("organization", schema="core_org")}

    # Add slug column to organization table
    if "slug" not in columns:
        op.add_column(
            "organization",
            sa.Column(
                "slug",
                sa.String(50),
                nullable=True,
                comment="URL-safe identifier for public pages like careers portal",
            ),
            schema="core_org",
        )
    # Create unique index on slug
    if "ix_core_org_organization_slug" not in indexes:
        op.create_index(
            "ix_core_org_organization_slug",
            "organization",
            ["slug"],
            unique=True,
            schema="core_org",
            postgresql_where=sa.text("slug IS NOT NULL"),
        )


def downgrade() -> None:
    op.drop_index(
        "ix_core_org_organization_slug",
        table_name="organization",
        schema="core_org",
    )
    op.drop_column("organization", "slug", schema="core_org")
