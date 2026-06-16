"""Add dotmac_sub integration: enum values + external-id columns.

Supports migrating the ISP-billing sync from Splynx to the dotmac_sub
subscriber-management system (selfcare.dotmac.io).

Revision ID: 20260616_dotmac_sub_integration
Revises: 20260615_fix_tax_device_schema
Create Date: 2026-06-16
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260616_dotmac_sub_integration"
down_revision = "20260615_fix_tax_device_schema"
branch_labels = None
depends_on = None

_COLUMNS: list[tuple[str, str, int]] = [
    ("customer", "dotmac_sub_id", 64),
    ("customer", "dotmac_sub_reseller_id", 64),
    ("invoice", "dotmac_sub_id", 64),
    ("invoice", "dotmac_sub_number", 100),
    ("customer_payment", "dotmac_sub_id", 64),
    ("customer_payment", "dotmac_sub_receipt_number", 100),
]


def _index_name(table: str, column: str) -> str:
    return f"ix_ar_{table}_{column}"


def upgrade() -> None:
    # Enum types are schema-qualified.
    op.execute("ALTER TYPE ar.external_source ADD VALUE IF NOT EXISTS 'DOTMAC_SUB'")
    op.execute("ALTER TYPE ar.sync_entity_type ADD VALUE IF NOT EXISTS 'RESELLER'")
    op.execute(
        "ALTER TYPE ar.sync_entity_type ADD VALUE IF NOT EXISTS 'BILLING_ACCOUNT'"
    )
    op.execute(
        "ALTER TYPE public.integration_type ADD VALUE IF NOT EXISTS 'DOTMAC_SUB'"
    )

    conn = op.get_bind()
    inspector = sa.inspect(conn)
    for table, column, length in _COLUMNS:
        existing_cols = {c["name"] for c in inspector.get_columns(table, schema="ar")}
        if column not in existing_cols:
            op.add_column(
                table,
                sa.Column(column, sa.String(length=length), nullable=True),
                schema="ar",
            )
        existing_idx = {ix["name"] for ix in inspector.get_indexes(table, schema="ar")}
        idx = _index_name(table, column)
        if idx not in existing_idx:
            op.create_index(idx, table, [column], schema="ar")


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    for table, column, _length in reversed(_COLUMNS):
        existing_idx = {ix["name"] for ix in inspector.get_indexes(table, schema="ar")}
        idx = _index_name(table, column)
        if idx in existing_idx:
            op.drop_index(idx, table_name=table, schema="ar")
        existing_cols = {c["name"] for c in inspector.get_columns(table, schema="ar")}
        if column in existing_cols:
            op.drop_column(table, column, schema="ar")
