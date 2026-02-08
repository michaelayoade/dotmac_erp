"""Add separator column to numbering_sequence table.

Revision ID: add_numbering_sequence_separator
Revises: add_banking_categorization
Create Date: 2025-02-12
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "add_numbering_sequence_separator"
down_revision = "add_banking_categorization"
branch_labels = None
depends_on = None


def _has_column(inspector, table_name: str, column_name: str, schema: str) -> bool:
    return column_name in {
        column["name"] for column in inspector.get_columns(table_name, schema=schema)
    }


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("numbering_sequence", schema="core_config"):
        return

    if not _has_column(inspector, "numbering_sequence", "separator", "core_config"):
        op.add_column(
            "numbering_sequence",
            sa.Column(
                "separator",
                sa.String(5),
                nullable=False,
                server_default="-",
            ),
            schema="core_config",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("numbering_sequence", schema="core_config"):
        return

    if _has_column(inspector, "numbering_sequence", "separator", "core_config"):
        op.drop_column("numbering_sequence", "separator", schema="core_config")
