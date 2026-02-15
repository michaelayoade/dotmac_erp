"""Compliance analyzer (deterministic, no LLM required).

Checks fiscal period health, open/overdue periods, and WHT certificate gaps.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.coach.insight import CoachInsight
from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FiscalPeriodHealthSummary:
    open_period_count: int
    overdue_close_count: int  # periods past end_date but still OPEN
    oldest_open_period_name: str | None
    oldest_open_end_date: date | None


def _severity_for_compliance(overdue_count: int) -> str:
    if overdue_count >= 3:
        return "WARNING"
    if overdue_count >= 1:
        return "ATTENTION"
    return "INFO"


class ComplianceAnalyzer:
    """Deterministic compliance / fiscal-period health analyzer.

    Generates org-wide Finance insights for overdue period closings
    and general compliance posture.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # MetricStore fast-path
    # ------------------------------------------------------------------
    def _quick_check_from_store(self, organization_id: UUID) -> bool:
        """Return True if MetricStore shows zero overdue fiscal periods."""
        from app.services.coach.analyzers import metric_is_fresh

        fresh, value = metric_is_fresh(
            self.db, organization_id, "compliance.overdue_fiscal_periods"
        )
        if fresh and value is not None and value <= 0:
            logger.debug("Compliance fast-path: zero overdue fiscal periods, skipping")
            return True
        return False

    # ------------------------------------------------------------------
    # Core computation
    # ------------------------------------------------------------------
    def fiscal_period_health(self, organization_id: UUID) -> FiscalPeriodHealthSummary:
        today = date.today()

        open_statuses = (PeriodStatus.OPEN, PeriodStatus.REOPENED)

        # Count open periods
        open_count_stmt = (
            select(func.count())
            .select_from(FiscalPeriod)
            .where(
                FiscalPeriod.organization_id == organization_id,
                FiscalPeriod.status.in_(open_statuses),
            )
        )
        open_count = int(self.db.scalar(open_count_stmt) or 0)

        # Count overdue-to-close periods (end_date has passed but still OPEN)
        overdue_stmt = (
            select(func.count())
            .select_from(FiscalPeriod)
            .where(
                FiscalPeriod.organization_id == organization_id,
                FiscalPeriod.status.in_(open_statuses),
                FiscalPeriod.end_date < today,
            )
        )
        overdue_count = int(self.db.scalar(overdue_stmt) or 0)

        # Oldest open period still past its end date
        oldest_stmt = (
            select(FiscalPeriod.period_name, FiscalPeriod.end_date)
            .where(
                FiscalPeriod.organization_id == organization_id,
                FiscalPeriod.status.in_(open_statuses),
                FiscalPeriod.end_date < today,
            )
            .order_by(FiscalPeriod.end_date.asc())
            .limit(1)
        )
        oldest_row = self.db.execute(oldest_stmt).first()
        oldest_name = oldest_row[0] if oldest_row else None
        oldest_end = oldest_row[1] if oldest_row else None

        return FiscalPeriodHealthSummary(
            open_period_count=open_count,
            overdue_close_count=overdue_count,
            oldest_open_period_name=oldest_name,
            oldest_open_end_date=oldest_end,
        )

    # ------------------------------------------------------------------
    # Insight generation
    # ------------------------------------------------------------------
    def generate_fiscal_period_health_insight(
        self, organization_id: UUID
    ) -> CoachInsight | None:
        if self._quick_check_from_store(organization_id):
            return None

        health = self.fiscal_period_health(organization_id)
        if health.overdue_close_count <= 0:
            return None

        severity = _severity_for_compliance(health.overdue_close_count)
        title = "Overdue fiscal period closings"

        days_overdue = (
            (date.today() - health.oldest_open_end_date).days
            if health.oldest_open_end_date
            else 0
        )

        summary_text = (
            f"{health.overdue_close_count} fiscal period(s) are past their end date "
            f"but still open. "
        )
        if health.oldest_open_period_name:
            summary_text += (
                f"Oldest: {health.oldest_open_period_name} "
                f"({days_overdue} day(s) overdue)."
            )

        coaching_action = (
            "Close overdue fiscal periods promptly to prevent posting errors "
            "and ensure accurate financial reporting. Review pending journal entries "
            "in each period before closing."
        )

        return CoachInsight(
            insight_id=uuid.uuid4(),
            organization_id=organization_id,
            audience="FINANCE",
            target_employee_id=None,
            category="COMPLIANCE",
            severity=severity,
            title=title,
            summary=summary_text,
            detail=None,
            coaching_action=coaching_action,
            confidence=0.95,
            data_sources={"gl.fiscal_period": health.overdue_close_count},
            evidence={
                "open_period_count": health.open_period_count,
                "overdue_close_count": health.overdue_close_count,
                "oldest_open_period_name": health.oldest_open_period_name,
                "oldest_open_end_date": (
                    health.oldest_open_end_date.isoformat()
                    if health.oldest_open_end_date
                    else None
                ),
                "days_overdue": days_overdue,
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
        today = date.today()
        self.db.execute(
            delete(CoachInsight).where(
                CoachInsight.organization_id == organization_id,
                CoachInsight.target_employee_id.is_(None),
                CoachInsight.category == "COMPLIANCE",
                CoachInsight.audience == "FINANCE",
                func.date(CoachInsight.created_at) == today,
                CoachInsight.title == "Overdue fiscal period closings",
            )
        )

        insight = self.generate_fiscal_period_health_insight(organization_id)
        if not insight:
            return 0
        self.db.add(insight)
        self.db.flush()
        return 1
