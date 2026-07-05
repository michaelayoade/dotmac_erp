"""Add ApiKey.scopes for least-privilege service keys.

Nullable JSON list of scope strings the key grants (e.g. ["crm:ncc:read"]).
NULL / empty = unscoped = full access, so every existing key keeps working;
a non-empty list restricts the key to those scopes. Backward-compatible.

Revision ID: 20260705_add_apikey_scopes
Revises: 20260705_customer_payment_dotmac_sub_unique
Create Date: 2026-07-05
"""

import sqlalchemy as sa

from alembic import op

revision = "20260705_add_apikey_scopes"
down_revision = "20260705_customer_payment_dotmac_sub_unique"
branch_labels = None
depends_on = None

_TABLE = "api_keys"
_COLUMN = "scopes"


def _has_column(inspector, table: str, column: str) -> bool:
    if not inspector.has_table(table):
        return False
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(_TABLE) or _has_column(inspector, _TABLE, _COLUMN):
        return
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _has_column(inspector, _TABLE, _COLUMN):
        op.drop_column(_TABLE, _COLUMN)
