"""Add ERPNext sync identifiers to additional synced models.

Revision ID: 20260212_add_erpnext_id_to_synced_models
Revises: e0696f5adbeb
Create Date: 2026-02-12
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260212_add_erpnext_id_to_synced_models"
down_revision = "e0696f5adbeb"
branch_labels = None
depends_on = None


TABLES = (
    ("gl", "account"),
    ("gl", "account_category"),
    ("fa", "asset"),
    ("fa", "asset_category"),
    ("inv", "item"),
    ("inv", "item_category"),
    ("inv", "warehouse"),
    ("inv", "inventory_transaction"),
    ("ap", "supplier"),
    ("pm", "time_entry"),
)


def _column_names(schema: str, table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {col["name"] for col in inspector.get_columns(table, schema=schema)}


def _index_names(schema: str, table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {idx["name"] for idx in inspector.get_indexes(table, schema=schema)}


def _erpnext_index_name(schema: str, table: str) -> str:
    return f"ix_{schema}_{table}_erpnext_id"


def upgrade() -> None:
    for schema, table in TABLES:
        columns = _column_names(schema, table)
        if "erpnext_id" not in columns:
            op.add_column(
                table,
                sa.Column(
                    "erpnext_id",
                    sa.String(length=255),
                    nullable=True,
                ),
                schema=schema,
            )
        if "last_synced_at" not in columns:
            op.add_column(
                table,
                sa.Column(
                    "last_synced_at",
                    sa.DateTime(timezone=True),
                    nullable=True,
                ),
                schema=schema,
            )

        index_name = _erpnext_index_name(schema, table)
        if index_name not in _index_names(schema, table):
            op.create_index(
                index_name,
                table,
                ["erpnext_id"],
                unique=False,
                schema=schema,
            )


def downgrade() -> None:
    for schema, table in TABLES:
        index_name = _erpnext_index_name(schema, table)
        if index_name in _index_names(schema, table):
            op.drop_index(index_name, table_name=table, schema=schema)

        columns = _column_names(schema, table)
        if "last_synced_at" in columns:
            op.drop_column(table, "last_synced_at", schema=schema)
        if "erpnext_id" in columns:
            op.drop_column(table, "erpnext_id", schema=schema)
