from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.coach.insight import CoachInsight
from app.models.finance.banking.bank_account import BankAccount, BankAccountStatus
from app.models.finance.banking.bank_reconciliation import (
    BankReconciliation,
    ReconciliationStatus,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StaleBankAccount:
    bank_account_id: UUID
    bank_name: str
    account_number: str
    last_approved_reconciliation_date: date | None
    days_stale: int


def _days_stale(last_date: date | None, today: date) -> int:
    if last_date is None:
        return 10_000  # effectively "unknown/never", will sort to top
    return max((today - last_date).days, 0)


def _severity_for_bank_recon(stale_count: int, max_days_stale: int) -> str:
    if stale_count <= 0:
        return "INFO"
    if max_days_stale >= 30:
        return "WARNING"
    return "ATTENTION"


class BankingHealthAnalyzer:
    """
    Deterministic banking health analyzer (no LLM required).

    Focus: reconciliation freshness across active bank accounts.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def _quick_check_from_store(self, organization_id: UUID) -> bool:
        """Return True if MetricStore shows zero unreconciled accounts."""
        from app.services.coach.analyzers import metric_is_fresh

        fresh, value = metric_is_fresh(
            self.db, organization_id, "efficiency.unreconciled_account_count"
        )
        if fresh and value is not None and value <= 0:
            logger.debug(
                "Banking fast-path: MetricStore shows zero stale accounts, skipping"
            )
            return True
        return False

    def stale_bank_accounts(
        self,
        organization_id: UUID,
        stale_after_days: int = 14,
    ) -> list[StaleBankAccount]:
        today = date.today()

        # Latest approved reconciliation date per bank account.
        last_approved = (
            select(
                BankReconciliation.bank_account_id.label("bank_account_id"),
                func.max(BankReconciliation.reconciliation_date).label("last_date"),
            )
            .where(
                BankReconciliation.organization_id == organization_id,
                BankReconciliation.status == ReconciliationStatus.approved,
            )
            .group_by(BankReconciliation.bank_account_id)
            .subquery()
        )

        stmt = (
            select(
                BankAccount.bank_account_id,
                BankAccount.bank_name,
                BankAccount.account_number,
                last_approved.c.last_date,
            )
            .outerjoin(
                last_approved,
                last_approved.c.bank_account_id == BankAccount.bank_account_id,
            )
            .where(
                BankAccount.organization_id == organization_id,
                BankAccount.status == BankAccountStatus.active,
            )
        )

        rows = self.db.execute(stmt).all()
        stale: list[StaleBankAccount] = []
        for bank_account_id, bank_name, account_number, last_date in rows:
            ds = _days_stale(last_date, today)
            if ds >= stale_after_days:
                stale.append(
                    StaleBankAccount(
                        bank_account_id=bank_account_id,
                        bank_name=str(bank_name),
                        account_number=str(account_number),
                        last_approved_reconciliation_date=last_date,
                        days_stale=ds,
                    )
                )

        stale.sort(key=lambda x: x.days_stale, reverse=True)
        return stale

    def generate_reconciliation_freshness_insight(
        self,
        organization_id: UUID,
    ) -> CoachInsight | None:
        if self._quick_check_from_store(organization_id):
            return None
        stale = self.stale_bank_accounts(organization_id)
        if not stale:
            return None

        max_days = max(a.days_stale for a in stale)
        severity = _severity_for_bank_recon(len(stale), max_days)

        top = stale[:5]
        top_lines = []
        for a in top:
            last = (
                a.last_approved_reconciliation_date.isoformat()
                if a.last_approved_reconciliation_date
                else "never"
            )
            top_lines.append(
                {
                    "bank_account_id": str(a.bank_account_id),
                    "bank_name": a.bank_name,
                    "account_number": a.account_number,
                    "last_approved_reconciliation_date": last,
                    "days_stale": a.days_stale,
                }
            )

        title = "Bank reconciliation freshness risk"
        summary = (
            f"{len(stale)} active bank account(s) have not been reconciled in 14+ days. "
            f"Worst case is {max_days} days stale."
        )
        coaching_action = (
            "Prioritize reconciling the stalest bank account(s) first. "
            "Fresh reconciliations reduce posting errors and improve cash visibility."
        )

        return CoachInsight(
            insight_id=uuid.uuid4(),
            organization_id=organization_id,
            audience="FINANCE",
            target_employee_id=None,
            category="EFFICIENCY",
            severity=severity,
            title=title,
            summary=summary,
            detail=None,
            coaching_action=coaching_action,
            confidence=0.9,
            data_sources={"banking.bank_accounts": len(stale)},
            evidence={
                "stale_account_count": len(stale),
                "max_days_stale": max_days,
                "top_stale_accounts": top_lines,
            },
            status="GENERATED",
            delivered_at=None,
            read_at=None,
            dismissed_at=None,
            feedback=None,
            valid_until=date.today() + timedelta(days=1),
            created_at=datetime.now(UTC),
        )

    def upsert_daily_org_insights(self, organization_id: UUID) -> int:
        """
        Delete+insert today's banking health insight(s) for determinism.
        """
        today = date.today()

        # Delete existing for today with same identity.
        self.db.execute(
            delete(CoachInsight).where(
                CoachInsight.organization_id == organization_id,
                CoachInsight.target_employee_id.is_(None),
                CoachInsight.category == "EFFICIENCY",
                CoachInsight.audience == "FINANCE",
                func.date(CoachInsight.created_at) == today,
                CoachInsight.title == "Bank reconciliation freshness risk",
            )
        )

        insight = self.generate_reconciliation_freshness_insight(organization_id)
        if not insight:
            return 0

        self.db.add(insight)
        self.db.flush()
        return 1
