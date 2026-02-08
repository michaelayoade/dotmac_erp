"""Add organization settings columns for contact, address, and branding.

Revision ID: add_organization_settings_columns
Revises: extend_alembic_version
Create Date: 2025-01-10
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "add_organization_settings_columns"
down_revision = "extend_alembic_version"
branch_labels = None
depends_on = None


def _has_column(inspector, table_name: str, column_name: str, schema: str) -> bool:
    return column_name in {
        column["name"] for column in inspector.get_columns(table_name, schema=schema)
    }


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("organization", schema="core_org"):
        return

    columns: list[tuple[str, sa.Column]] = [
        # Regional settings
        ("timezone", sa.Column("timezone", sa.String(50), nullable=True)),
        ("date_format", sa.Column("date_format", sa.String(20), nullable=True)),
        ("number_format", sa.Column("number_format", sa.String(20), nullable=True)),
        # Contact information
        ("contact_email", sa.Column("contact_email", sa.String(255), nullable=True)),
        ("contact_phone", sa.Column("contact_phone", sa.String(50), nullable=True)),
        # Address
        ("address_line1", sa.Column("address_line1", sa.String(255), nullable=True)),
        ("address_line2", sa.Column("address_line2", sa.String(255), nullable=True)),
        ("city", sa.Column("city", sa.String(100), nullable=True)),
        ("state", sa.Column("state", sa.String(100), nullable=True)),
        ("postal_code", sa.Column("postal_code", sa.String(20), nullable=True)),
        ("country", sa.Column("country", sa.String(100), nullable=True)),
        # Branding
        ("logo_url", sa.Column("logo_url", sa.String(500), nullable=True)),
        ("website_url", sa.Column("website_url", sa.String(255), nullable=True)),
    ]

    for column_name, column in columns:
        if not _has_column(inspector, "organization", column_name, "core_org"):
            op.add_column("organization", column, schema="core_org")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("organization", schema="core_org"):
        return

    column_names = [
        "website_url",
        "logo_url",
        "country",
        "postal_code",
        "state",
        "city",
        "address_line2",
        "address_line1",
        "contact_phone",
        "contact_email",
        "number_format",
        "date_format",
        "timezone",
    ]

    for column_name in column_names:
        if _has_column(inspector, "organization", column_name, "core_org"):
            op.drop_column("organization", column_name, schema="core_org")
