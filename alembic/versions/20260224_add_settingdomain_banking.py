"""Add 'banking' value to SettingDomain PostgreSQL enum.

Revision ID: 20260224_add_settingdomain_banking
Revises: 20260223_add_approver_budget_adjustment
Create Date: 2026-02-24
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "20260224_add_settingdomain_banking"
down_revision: Union[str, None] = "20260224_add_goods_receipt_line_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: only add if not already present
    conn = op.get_bind()
    exists = conn.exec_driver_sql(
        "SELECT 1 FROM pg_enum WHERE enumlabel = 'banking' "
        "AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'settingdomain')"
    ).fetchone()
    if not exists:
        op.execute("ALTER TYPE settingdomain ADD VALUE 'banking'")


def downgrade() -> None:
    # PostgreSQL cannot remove enum values safely; no-op.
    pass
