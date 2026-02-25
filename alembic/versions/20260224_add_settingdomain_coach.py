"""Add 'coach' value to SettingDomain PostgreSQL enum.

Revision ID: 20260224_add_settingdomain_coach
Revises: 20260224_add_recon_match_rules_and_field_tracking
Create Date: 2026-02-24
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "20260224_add_settingdomain_coach"
down_revision: Union[str, None] = "20260224_add_recon_match_rules_and_field_tracking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: only add if not already present
    conn = op.get_bind()
    exists = conn.exec_driver_sql(
        "SELECT 1 FROM pg_enum WHERE enumlabel = 'coach' "
        "AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'settingdomain')"
    ).fetchone()
    if not exists:
        op.execute("ALTER TYPE settingdomain ADD VALUE 'coach'")


def downgrade() -> None:
    # PostgreSQL cannot remove enum values safely; no-op.
    pass
