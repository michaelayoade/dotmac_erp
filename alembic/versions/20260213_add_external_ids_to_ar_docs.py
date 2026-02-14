"""Add erpnext_id, splynx_id, and last_synced_at to AR invoice and customer_payment.

Revision ID: 20260213_add_external_ids_to_ar_docs
Revises: 20260212_add_bank_statement_sequence_type
Create Date: 2026-02-13
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260213_add_external_ids_to_ar_docs"
down_revision = "20260212_add_bank_statement_sequence_type"
branch_labels = None
depends_on = None

# Tables that need both erpnext_id and splynx_id (dual-source)
DUAL_SOURCE_TABLES = (
    ("ar", "invoice"),
    ("ar", "customer_payment"),
)


def _column_names(schema: str, table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {col["name"] for col in inspector.get_columns(table, schema=schema)}


def _index_names(schema: str, table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {idx["name"] for idx in inspector.get_indexes(table, schema=schema)}


def upgrade() -> None:
    for schema, table in DUAL_SOURCE_TABLES:
        columns = _column_names(schema, table)

        # Add erpnext_id
        if "erpnext_id" not in columns:
            op.add_column(
                table,
                sa.Column("erpnext_id", sa.String(length=255), nullable=True),
                schema=schema,
            )

        # Add splynx_id
        if "splynx_id" not in columns:
            op.add_column(
                table,
                sa.Column("splynx_id", sa.String(length=100), nullable=True),
                schema=schema,
            )

        # Add last_synced_at
        if "last_synced_at" not in columns:
            op.add_column(
                table,
                sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
                schema=schema,
            )

        # Partial indexes on non-null values for efficient lookups
        indexes = _index_names(schema, table)

        idx_erpnext = f"ix_{schema}_{table}_erpnext_id"
        if idx_erpnext not in indexes:
            op.execute(
                f"CREATE INDEX {idx_erpnext} ON {schema}.{table} (erpnext_id) "
                f"WHERE erpnext_id IS NOT NULL"
            )

        idx_splynx = f"ix_{schema}_{table}_splynx_id"
        if idx_splynx not in indexes:
            op.execute(
                f"CREATE INDEX {idx_splynx} ON {schema}.{table} (splynx_id) "
                f"WHERE splynx_id IS NOT NULL"
            )


def downgrade() -> None:
    for schema, table in DUAL_SOURCE_TABLES:
        indexes = _index_names(schema, table)
        columns = _column_names(schema, table)

        for idx_name in (
            f"ix_{schema}_{table}_splynx_id",
            f"ix_{schema}_{table}_erpnext_id",
        ):
            if idx_name in indexes:
                op.drop_index(idx_name, table_name=table, schema=schema)

        if "last_synced_at" in columns:
            op.drop_column(table, "last_synced_at", schema=schema)
        if "splynx_id" in columns:
            op.drop_column(table, "splynx_id", schema=schema)
        if "erpnext_id" in columns:
            op.drop_column(table, "erpnext_id", schema=schema)
