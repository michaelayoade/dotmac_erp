"""Add 'expense' value to SettingDomain PostgreSQL enum.

Revision ID: 20260310_add_settingdomain_expense
Revises: 0ee821d92395
Create Date: 2026-03-10 11:57:13
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "20260310_add_settingdomain_expense"
down_revision: Union[str, None] = "0ee821d92395"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: only add if not already present.
    conn = op.get_bind()
    exists = conn.exec_driver_sql(
        "SELECT 1 FROM pg_enum WHERE enumlabel = 'expense' "
        "AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'settingdomain')"
    ).fetchone()
    if not exists:
        op.execute("ALTER TYPE settingdomain ADD VALUE 'expense'")


def downgrade() -> None:
    # PostgreSQL cannot remove enum values safely; no-op.
    pass
