"""Add missing ERPNext sync columns to core_org.project.

Revision ID: 20260312_add_missing_project_sync_columns
Revises: 964b7e6deaf7
Create Date: 2026-03-12 14:30:00
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "20260312_add_missing_project_sync_columns"
down_revision: Union[str, None] = "964b7e6deaf7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "core_org"
TABLE = "project"


def _has_table(inspector: sa.Inspector, schema: str, table: str) -> bool:
    return inspector.has_table(table, schema=schema)


def _column_names(inspector: sa.Inspector, schema: str, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table, schema=schema)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, SCHEMA, TABLE):
        return

    columns = _column_names(inspector, SCHEMA, TABLE)

    if "erpnext_id" not in columns:
        op.add_column(
            TABLE,
            sa.Column("erpnext_id", sa.String(length=255), nullable=True),
            schema=SCHEMA,
        )

    if "last_synced_at" not in columns:
        op.add_column(
            TABLE,
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            schema=SCHEMA,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, SCHEMA, TABLE):
        return

    columns = _column_names(inspector, SCHEMA, TABLE)

    if "last_synced_at" in columns:
        op.drop_column(TABLE, "last_synced_at", schema=SCHEMA)

    if "erpnext_id" in columns:
        op.drop_column(TABLE, "erpnext_id", schema=SCHEMA)
