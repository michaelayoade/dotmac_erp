"""
Outbox consequence reconciler — verifies applied results against truth.

For recent ``ledger.posting.completed`` events settled as PUBLISHED, the
declared consequence is the GL balance projection (``gl.account_balance``)
built from ``gl.posted_ledger_line``. This service recomputes the expected
projection from the posted lines (the authoritative source) and compares it
to the cached balance rows. Drift is repaired through the existing canonical
writer — :meth:`AccountBalanceService.rebuild_balances_for_period` — the
same code path the daily ``rebuild_account_balances`` safety net uses, so
no second computation of the projection is introduced.

Idempotent: verification is read-only; repair rebuilds a period from
authoritative inputs, so running twice converges to the same state.

All methods flush only; the calling task owns commit/rollback.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.finance.gl.account_balance import AccountBalance, BalanceType
from app.models.finance.gl.posted_ledger_line import PostedLedgerLine
from app.models.finance.platform.event_outbox import EventOutbox, EventStatus

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")


class OutboxBalanceReconciler:
    """Compares outbox delivery success with the GL balance projection."""

    EVENT_NAME = "ledger.posting.completed"

    @staticmethod
    def collect_recent_posting_scopes(
        db: Session,
        since: datetime,
    ) -> list[tuple[UUID, UUID]]:
        """List distinct (organization_id, fiscal_period_id) scopes touched
        by recently PUBLISHED posting events.

        Excludes events with a terminal_reason note (declared no-op
        settlements) — only applied results are verified.

        Args:
            db: Cross-org session (event_outbox is not org-scoped)
            since: Only events published at/after this time

        Returns:
            De-duplicated list of (organization_id, fiscal_period_id)
        """
        events = db.scalars(
            select(EventOutbox).where(
                and_(
                    EventOutbox.event_name == OutboxBalanceReconciler.EVENT_NAME,
                    EventOutbox.status == EventStatus.PUBLISHED,
                    EventOutbox.published_at.isnot(None),
                    EventOutbox.published_at >= since,
                    EventOutbox.terminal_reason.is_(None),
                )
            )
        ).all()

        scopes: set[tuple[UUID, UUID]] = set()
        for event in events:
            payload = event.payload or {}
            org_raw = payload.get("organization_id")
            period_raw = payload.get("fiscal_period_id")
            if not org_raw or not period_raw:
                logger.warning(
                    "Reconciler skipping event %s: payload missing "
                    "organization_id/fiscal_period_id",
                    event.event_id,
                )
                continue
            try:
                scopes.add((UUID(str(org_raw)), UUID(str(period_raw))))
            except ValueError:
                logger.warning(
                    "Reconciler skipping event %s: malformed scope identifiers",
                    event.event_id,
                )
        return sorted(scopes, key=str)

    @staticmethod
    def find_period_drift(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
    ) -> list[dict[str, Any]]:
        """Compare expected per-account period movement (from posted ledger
        lines) against the cached balance projection for one period.

        Read-only. Returns one record per drifted account (empty = clean).
        """
        expected_rows = db.execute(
            select(
                PostedLedgerLine.account_id,
                func.coalesce(func.sum(PostedLedgerLine.debit_amount), 0).label(
                    "debit"
                ),
                func.coalesce(func.sum(PostedLedgerLine.credit_amount), 0).label(
                    "credit"
                ),
            )
            .where(
                and_(
                    PostedLedgerLine.organization_id == organization_id,
                    PostedLedgerLine.fiscal_period_id == fiscal_period_id,
                )
            )
            .group_by(PostedLedgerLine.account_id)
        ).all()

        actual_rows = db.execute(
            select(
                AccountBalance.account_id,
                func.coalesce(func.sum(AccountBalance.period_debit), 0).label("debit"),
                func.coalesce(func.sum(AccountBalance.period_credit), 0).label(
                    "credit"
                ),
            )
            .where(
                and_(
                    AccountBalance.organization_id == organization_id,
                    AccountBalance.fiscal_period_id == fiscal_period_id,
                    AccountBalance.balance_type == BalanceType.ACTUAL,
                )
            )
            .group_by(AccountBalance.account_id)
        ).all()

        expected = {
            row.account_id: (Decimal(row.debit), Decimal(row.credit))
            for row in expected_rows
        }
        actual = {
            row.account_id: (Decimal(row.debit), Decimal(row.credit))
            for row in actual_rows
        }

        drift: list[dict[str, Any]] = []
        for account_id in sorted(set(expected) | set(actual), key=str):
            exp_debit, exp_credit = expected.get(account_id, (_ZERO, _ZERO))
            act_debit, act_credit = actual.get(account_id, (_ZERO, _ZERO))
            if exp_debit != act_debit or exp_credit != act_credit:
                drift.append(
                    {
                        "account_id": str(account_id),
                        "expected_debit": str(exp_debit),
                        "expected_credit": str(exp_credit),
                        "actual_debit": str(act_debit),
                        "actual_credit": str(act_credit),
                    }
                )
        return drift

    @staticmethod
    def reconcile_period(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
    ) -> dict[str, Any]:
        """Verify one period and repair drift through the canonical writer.

        Flush only — the calling task commits. Returns a record of what
        was found and repaired.
        """
        drift = OutboxBalanceReconciler.find_period_drift(
            db, organization_id, fiscal_period_id
        )
        record: dict[str, Any] = {
            "organization_id": str(organization_id),
            "fiscal_period_id": str(fiscal_period_id),
            "drifted_accounts": len(drift),
            "drift": drift,
            "repaired": False,
            "rebuilt_balance_records": 0,
        }
        if not drift:
            return record

        # Repair through the existing canonical writer (same path as the
        # daily rebuild_account_balances safety net).
        from app.services.finance.gl.account_balance import AccountBalanceService

        logger.warning(
            "Outbox reconciler found balance drift for org %s period %s "
            "(%d accounts); rebuilding from posted ledger lines",
            organization_id,
            fiscal_period_id,
            len(drift),
        )
        count = AccountBalanceService.rebuild_balances_for_period(
            db,
            organization_id=organization_id,
            fiscal_period_id=fiscal_period_id,
        )
        record["repaired"] = True
        record["rebuilt_balance_records"] = count
        return record
