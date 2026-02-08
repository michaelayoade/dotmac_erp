"""Backfill missing columns on numbering_sequence.

Revision ID: add_numbering_sequence_columns
Revises: add_numbering_sequence_separator
Create Date: 2025-02-12
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.alembic_utils import ensure_enum

# revision identifiers, used by Alembic.
revision = "add_numbering_sequence_columns"
down_revision = "add_numbering_sequence_separator"
branch_labels = None
depends_on = None


def _has_column(inspector, table_name: str, column_name: str, schema: str) -> bool:
    return column_name in {
        column["name"] for column in inspector.get_columns(table_name, schema=schema)
    }


def _ensure_reset_frequency_type() -> None:
    bind = op.get_bind()
    ensure_enum(bind, "reset_frequency", "NEVER", "YEARLY", "MONTHLY")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("numbering_sequence", schema="core_config"):
        return

    _ensure_reset_frequency_type()

    columns: list[tuple[str, sa.Column]] = [
        (
            "prefix",
            sa.Column("prefix", sa.String(20), nullable=False, server_default=""),
        ),
        (
            "suffix",
            sa.Column("suffix", sa.String(10), nullable=False, server_default=""),
        ),
        (
            "separator",
            sa.Column("separator", sa.String(5), nullable=False, server_default="-"),
        ),
        (
            "min_digits",
            sa.Column("min_digits", sa.Integer(), nullable=False, server_default="4"),
        ),
        (
            "include_year",
            sa.Column(
                "include_year", sa.Boolean(), nullable=False, server_default="true"
            ),
        ),
        (
            "include_month",
            sa.Column(
                "include_month", sa.Boolean(), nullable=False, server_default="true"
            ),
        ),
        (
            "year_format",
            sa.Column("year_format", sa.Integer(), nullable=False, server_default="4"),
        ),
        (
            "current_number",
            sa.Column(
                "current_number", sa.BigInteger(), nullable=False, server_default="0"
            ),
        ),
        ("current_year", sa.Column("current_year", sa.Integer(), nullable=True)),
        ("current_month", sa.Column("current_month", sa.Integer(), nullable=True)),
        (
            "reset_frequency",
            sa.Column(
                "reset_frequency",
                postgresql.ENUM(
                    "NEVER",
                    "YEARLY",
                    "MONTHLY",
                    name="reset_frequency",
                    create_type=False,
                ),
                nullable=False,
                server_default="MONTHLY",
            ),
        ),
        (
            "fiscal_year_reset",
            sa.Column(
                "fiscal_year_reset",
                sa.Boolean(),
                nullable=False,
                server_default="false",
            ),
        ),
        (
            "fiscal_year_id",
            sa.Column("fiscal_year_id", postgresql.UUID(as_uuid=True), nullable=True),
        ),
        (
            "last_used_at",
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        ),
        (
            "created_at",
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        ),
        (
            "updated_at",
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        ),
    ]

    for column_name, column in columns:
        if not _has_column(inspector, "numbering_sequence", column_name, "core_config"):
            op.add_column("numbering_sequence", column, schema="core_config")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("numbering_sequence", schema="core_config"):
        return

    # Drop in reverse order to avoid dependency issues.
    column_names = [
        "updated_at",
        "created_at",
        "last_used_at",
        "fiscal_year_id",
        "fiscal_year_reset",
        "reset_frequency",
        "current_month",
        "current_year",
        "current_number",
        "year_format",
        "include_month",
        "include_year",
        "min_digits",
        "separator",
        "suffix",
        "prefix",
    ]

    for column_name in column_names:
        if _has_column(inspector, "numbering_sequence", column_name, "core_config"):
            op.drop_column("numbering_sequence", column_name, schema="core_config")
