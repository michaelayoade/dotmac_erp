"""
Balance refresh service.

Processes queued account balance refresh requests and recalculates period
movements from posted ledger lines.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.finance.gl.account_balance import AccountBalance, BalanceType
from app.models.finance.gl.balance_refresh_queue import BalanceRefreshQueue
from app.models.finance.gl.posted_ledger_line import PostedLedgerLine
from app.services.common import coerce_uuid
from app.services.finance.platform.org_context import org_context_service

logger = logging.getLogger(__name__)


class BalanceRefreshService:
    """Recalculate stale balances from posted ledger lines."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def process_queue(self, batch_size: int = 100) -> dict[str, int]:
        """Refresh pending queue entries and mark them processed."""
        pending = list(
            self.db.scalars(
                select(BalanceRefreshQueue)
                .where(BalanceRefreshQueue.processed_at.is_(None))
                .order_by(BalanceRefreshQueue.invalidated_at.asc())
                .limit(batch_size)
            ).all()
        )

        results = {"processed": len(pending), "refreshed": 0, "errors": 0}
        now = datetime.now(UTC)
        for entry in pending:
            try:
                self._refresh_balance(
                    organization_id=entry.organization_id,
                    account_id=entry.account_id,
                    fiscal_period_id=entry.fiscal_period_id,
                )
                entry.processed_at = now
                results["refreshed"] += 1
            except Exception:
                logger.exception(
                    "Failed refreshing balance org=%s account=%s period=%s",
                    entry.organization_id,
                    entry.account_id,
                    entry.fiscal_period_id,
                )
                results["errors"] += 1

        self.db.flush()
        return results

    def _refresh_balance(
        self,
        organization_id: UUID,
        account_id: UUID,
        fiscal_period_id: UUID,
    ) -> int:
        """Refresh all dimensioned balances for one account + period."""
        org_id = coerce_uuid(organization_id)
        acct_id = coerce_uuid(account_id)
        period_id = coerce_uuid(fiscal_period_id)
        now = datetime.now(UTC)
        currency_code = org_context_service.get_functional_currency(self.db, org_id)

        grouped = self.db.execute(
            select(
                PostedLedgerLine.business_unit_id,
                PostedLedgerLine.cost_center_id,
                PostedLedgerLine.project_id,
                PostedLedgerLine.segment_id,
                func.coalesce(func.sum(PostedLedgerLine.debit_amount), 0).label(
                    "period_debit"
                ),
                func.coalesce(func.sum(PostedLedgerLine.credit_amount), 0).label(
                    "period_credit"
                ),
                func.count().label("tx_count"),
            )
            .where(
                and_(
                    PostedLedgerLine.organization_id == org_id,
                    PostedLedgerLine.account_id == acct_id,
                    PostedLedgerLine.fiscal_period_id == period_id,
                )
            )
            .group_by(
                PostedLedgerLine.business_unit_id,
                PostedLedgerLine.cost_center_id,
                PostedLedgerLine.project_id,
                PostedLedgerLine.segment_id,
            )
        ).all()

        existing_balances = list(
            self.db.scalars(
                select(AccountBalance).where(
                    and_(
                        AccountBalance.organization_id == org_id,
                        AccountBalance.account_id == acct_id,
                        AccountBalance.fiscal_period_id == period_id,
                        AccountBalance.balance_type == BalanceType.ACTUAL,
                    )
                )
            ).all()
        )

        def _key(
            business_unit_id: UUID | None,
            cost_center_id: UUID | None,
            project_id: UUID | None,
            segment_id: UUID | None,
            code: str,
        ) -> tuple[UUID | None, UUID | None, UUID | None, UUID | None, str]:
            return (
                business_unit_id,
                cost_center_id,
                project_id,
                segment_id,
                code,
            )

        existing_map = {
            _key(
                bal.business_unit_id,
                bal.cost_center_id,
                bal.project_id,
                bal.segment_id,
                bal.currency_code,
            ): bal
            for bal in existing_balances
        }
        touched: set[tuple[UUID | None, UUID | None, UUID | None, UUID | None, str]] = (
            set()
        )

        for row in grouped:
            row_key = _key(
                row.business_unit_id,
                row.cost_center_id,
                row.project_id,
                row.segment_id,
                currency_code,
            )
            touched.add(row_key)
            period_debit = Decimal(str(row.period_debit or 0))
            period_credit = Decimal(str(row.period_credit or 0))
            tx_count = int(row.tx_count or 0)

            balance = existing_map.get(row_key)
            if balance is None:
                balance = AccountBalance(
                    organization_id=org_id,
                    account_id=acct_id,
                    fiscal_period_id=period_id,
                    balance_type=BalanceType.ACTUAL,
                    currency_code=currency_code,
                    business_unit_id=row.business_unit_id,
                    cost_center_id=row.cost_center_id,
                    project_id=row.project_id,
                    segment_id=row.segment_id,
                    opening_debit=Decimal("0"),
                    opening_credit=Decimal("0"),
                    period_debit=Decimal("0"),
                    period_credit=Decimal("0"),
                    closing_debit=Decimal("0"),
                    closing_credit=Decimal("0"),
                    net_balance=Decimal("0"),
                    transaction_count=0,
                    refresh_count=0,
                )
                self.db.add(balance)
                existing_map[row_key] = balance

            balance.period_debit = period_debit
            balance.period_credit = period_credit
            balance.closing_debit = balance.opening_debit + period_debit
            balance.closing_credit = balance.opening_credit + period_credit
            balance.net_balance = balance.closing_debit - balance.closing_credit
            balance.transaction_count = tx_count
            balance.is_stale = False
            balance.stale_since = None
            balance.refresh_count += 1
            balance.last_updated_at = now

        for row_key, balance in existing_map.items():
            if row_key in touched:
                continue
            balance.period_debit = Decimal("0")
            balance.period_credit = Decimal("0")
            balance.closing_debit = balance.opening_debit
            balance.closing_credit = balance.opening_credit
            balance.net_balance = balance.opening_debit - balance.opening_credit
            balance.transaction_count = 0
            balance.is_stale = False
            balance.stale_since = None
            balance.refresh_count += 1
            balance.last_updated_at = now

        self.db.flush()
        return len(existing_map)
