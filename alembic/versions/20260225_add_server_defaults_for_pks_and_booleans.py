"""Add server_default for UUID PKs and boolean/integer columns.

Adds gen_random_uuid() defaults to PK columns and appropriate defaults
to boolean/integer columns on fiscal_position, field_change_log, and
reconciliation_match_rule tables.

Revision ID: 20260225_add_server_defaults_for_pks_and_booleans
Revises: 20260225_expand_service_hook_handlers
Create Date: 2026-02-25
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "20260225_add_server_defaults_for_pks_and_booleans"
down_revision: Union[str, Sequence[str], None] = "20260225_expand_service_hook_handlers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (schema, table, column, server_default_expression)
_COLUMNS: list[tuple[str, str, str, str]] = [
    ("tax", "fiscal_position", "fiscal_position_id", "gen_random_uuid()"),
    ("tax", "fiscal_position", "auto_apply", "false"),
    ("tax", "fiscal_position", "priority", "10"),
    ("tax", "fiscal_position", "is_active", "true"),
    ("tax", "fiscal_position_tax_map", "id", "gen_random_uuid()"),
    ("tax", "fiscal_position_account_map", "id", "gen_random_uuid()"),
    ("audit", "field_change_log", "log_id", "gen_random_uuid()"),
    ("banking", "reconciliation_match_rule", "rule_id", "gen_random_uuid()"),
    ("banking", "reconciliation_match_log", "log_id", "gen_random_uuid()"),
]


def _col_has_default(
    inspector: sa.engine.reflection.Inspector,
    schema: str,
    table: str,
    column: str,
) -> bool:
    """Return True if the column already has a server_default."""
    if not inspector.has_table(table, schema=schema):
        return True  # table missing — nothing to do
    for col in inspector.get_columns(table, schema=schema):
        if col["name"] == column:
            return col.get("default") is not None
    return True  # column missing — nothing to do


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    for schema, table, column, default_expr in _COLUMNS:
        if not _col_has_default(inspector, schema, table, column):
            op.alter_column(
                table,
                column,
                server_default=sa.text(default_expr),
                schema=schema,
            )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    for schema, table, column, _default_expr in _COLUMNS:
        if inspector.has_table(table, schema=schema):
            op.alter_column(
                table,
                column,
                server_default=None,
                schema=schema,
            )
