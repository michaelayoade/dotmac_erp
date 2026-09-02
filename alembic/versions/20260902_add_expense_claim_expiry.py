"""Add expense claim expiry status and action.

Revision ID: 20260902_expense_claim_expiry
Revises: 20260831_sync_history_heartbeat
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260902_expense_claim_expiry"
down_revision: str = "20260831_sync_history_heartbeat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE expense.expense_claim_status "
        "ADD VALUE IF NOT EXISTS 'EXPIRED'"
    )
    op.execute(
        "ALTER TYPE expense_claim_action_type "
        "ADD VALUE IF NOT EXISTS 'EXPIRE'"
    )


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed safely while dependent rows exist.
    pass
