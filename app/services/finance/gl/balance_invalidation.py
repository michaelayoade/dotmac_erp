"""
Balance invalidation service.

Marks account balances stale and queues account/period refresh work.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.finance.gl.account_balance import AccountBalance, BalanceType
from app.models.finance.gl.balance_refresh_queue import BalanceRefreshQueue
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)


class BalanceInvalidationService:
    """Marks account balances stale after ledger posting events."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def invalidate(
        self,
        organization_id: UUID,
        account_id: UUID,
        fiscal_period_id: UUID,
    ) -> None:
        """Mark account balances stale and enqueue refresh work."""
        org_id = organization_id
        acct_id = account_id
        period_id = fiscal_period_id
        now = datetime.now(UTC)

        self.db.execute(
            update(AccountBalance)
            .where(
                AccountBalance.organization_id == org_id,
                AccountBalance.account_id == acct_id,
                AccountBalance.fiscal_period_id == period_id,
                AccountBalance.balance_type == BalanceType.ACTUAL,
            )
            .values(is_stale=True, stale_since=now)
        )

        queue_entry = self.db.scalar(
            select(BalanceRefreshQueue).where(
                BalanceRefreshQueue.organization_id == org_id,
                BalanceRefreshQueue.account_id == acct_id,
                BalanceRefreshQueue.fiscal_period_id == period_id,
            )
        )
        if queue_entry:
            queue_entry.invalidated_at = now
            queue_entry.processed_at = None
        else:
            self.db.add(
                BalanceRefreshQueue(
                    organization_id=org_id,
                    account_id=acct_id,
                    fiscal_period_id=period_id,
                    invalidated_at=now,
                )
            )

        self.db.flush()

    def invalidate_batch(self, entries: list[tuple[UUID, UUID, UUID]]) -> int:
        """Bulk invalidate unique (org, account, period) tuples."""
        unique_entries = {
            (
                coerce_uuid(org_id),
                coerce_uuid(account_id),
                coerce_uuid(period_id),
            )
            for org_id, account_id, period_id in entries
        }
        for org_id, account_id, period_id in unique_entries:
            self.invalidate(org_id, account_id, period_id)
        if unique_entries:
            logger.debug("Queued %d balance refresh keys", len(unique_entries))
        return len(unique_entries)
