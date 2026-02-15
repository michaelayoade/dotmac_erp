from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.coach.insight import CoachInsight
from app.models.expense.expense_claim import ExpenseClaim, ExpenseClaimStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PendingApprovalsSummary:
    total_pending: int
    missing_approver: int
    oldest_created_at: datetime | None

    @property
    def max_age_days(self) -> int:
        if self.oldest_created_at is None:
            return 0
        created = self.oldest_created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return max((datetime.now(UTC) - created).days, 0)


def _severity_for_pending_approvals(total_pending: int, max_age_days: int) -> str:
    if total_pending <= 0:
        return "INFO"
    if max_age_days >= 14:
        return "WARNING"
    return "ATTENTION"


class ExpenseApprovalAnalyzer:
    """
    Deterministic expense approval analyzer (no LLM required).

    Generates:
    - Per-approver targeted backlog insight (target_employee_id=approver_id)
    - Org-wide hygiene insight for missing approver assignment
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def _quick_check_from_store(self, organization_id: UUID) -> bool:
        """Return True if MetricStore shows zero pending expense approvals."""
        from app.services.coach.analyzers import metric_is_fresh

        fresh, value = metric_is_fresh(
            self.db, organization_id, "efficiency.pending_expense_approvals"
        )
        if fresh and value is not None and value <= 0:
            logger.debug(
                "Expense fast-path: MetricStore shows zero pending approvals, skipping"
            )
            return True
        return False

    def pending_approvals_for_approver(
        self,
        organization_id: UUID,
        approver_employee_id: UUID,
    ) -> PendingApprovalsSummary:
        stmt = select(
            func.count().label("cnt"),
            func.min(ExpenseClaim.created_at).label("oldest"),
        ).where(
            ExpenseClaim.organization_id == organization_id,
            ExpenseClaim.status == ExpenseClaimStatus.PENDING_APPROVAL,
            ExpenseClaim.approver_id == approver_employee_id,
        )
        row = self.db.execute(stmt).one()
        total_pending = int(row.cnt or 0)
        oldest = row.oldest
        return PendingApprovalsSummary(
            total_pending=total_pending,
            missing_approver=0,
            oldest_created_at=oldest,
        )

    def top_pending_approvers(
        self,
        organization_id: UUID,
        limit: int = 20,
    ) -> list[tuple[UUID, int, datetime | None]]:
        """
        Return approver_id, pending_count, oldest_created_at for top approvers.
        """
        stmt = (
            select(
                ExpenseClaim.approver_id,
                func.count().label("cnt"),
                func.min(ExpenseClaim.created_at).label("oldest"),
            )
            .where(
                ExpenseClaim.organization_id == organization_id,
                ExpenseClaim.status == ExpenseClaimStatus.PENDING_APPROVAL,
                ExpenseClaim.approver_id.is_not(None),
            )
            .group_by(ExpenseClaim.approver_id)
            .order_by(func.count().desc())
            .limit(limit)
        )
        rows = self.db.execute(stmt).all()
        out: list[tuple[UUID, int, datetime | None]] = []
        for approver_id, cnt, oldest in rows:
            if approver_id is None:
                continue
            out.append((approver_id, int(cnt or 0), oldest))
        return out

    def generate_approver_backlog_insight(
        self,
        organization_id: UUID,
        approver_employee_id: UUID,
        summary: PendingApprovalsSummary,
    ) -> CoachInsight | None:
        if summary.total_pending <= 0:
            return None

        max_age_days = summary.max_age_days
        severity = _severity_for_pending_approvals(summary.total_pending, max_age_days)
        title = "Expense approvals pending"
        detail = None
        summary_text = (
            f"You have {summary.total_pending} expense claim(s) pending approval. "
            f"Oldest is {max_age_days} day(s) old."
        )
        coaching_action = (
            "Clear the oldest approvals first to reduce reimbursement delays."
        )

        return CoachInsight(
            insight_id=uuid.uuid4(),
            organization_id=organization_id,
            audience="FINANCE",
            target_employee_id=approver_employee_id,
            category="EFFICIENCY",
            severity=severity,
            title=title,
            summary=summary_text,
            detail=detail,
            coaching_action=coaching_action,
            confidence=0.9,
            data_sources={"expense.expense_claim": summary.total_pending},
            evidence={
                "pending_count": summary.total_pending,
                "max_age_days": max_age_days,
            },
            status="GENERATED",
            delivered_at=None,
            read_at=None,
            dismissed_at=None,
            feedback=None,
            valid_until=date.today() + timedelta(days=1),
            created_at=datetime.now(UTC),
        )

    def generate_missing_approver_insight(
        self,
        organization_id: UUID,
        missing_count: int,
    ) -> CoachInsight | None:
        if missing_count <= 0:
            return None

        title = "Expense claims missing approver assignment"
        summary = (
            f"{missing_count} submitted expense claim(s) are pending approval without an "
            "assigned approver. These will stall until an approver is set."
        )
        coaching_action = "Assign approvers for pending claims (or fix routing rules) so approvals can proceed."

        return CoachInsight(
            insight_id=uuid.uuid4(),
            organization_id=organization_id,
            audience="FINANCE",
            target_employee_id=None,
            category="EFFICIENCY",
            severity="WARNING",
            title=title,
            summary=summary,
            detail=None,
            coaching_action=coaching_action,
            confidence=0.95,
            data_sources={"expense.expense_claim": missing_count},
            evidence={"missing_approver_pending_count": missing_count},
            status="GENERATED",
            delivered_at=None,
            read_at=None,
            dismissed_at=None,
            feedback=None,
            valid_until=date.today() + timedelta(days=1),
            created_at=datetime.now(UTC),
        )

    def upsert_daily_insights(self, organization_id: UUID, limit: int = 20) -> int:
        today = date.today()
        written = 0

        # Clear today's generated insights for deterministic reruns.
        self.db.execute(
            delete(CoachInsight).where(
                CoachInsight.organization_id == organization_id,
                CoachInsight.category == "EFFICIENCY",
                CoachInsight.audience == "FINANCE",
                func.date(CoachInsight.created_at) == today,
                CoachInsight.title.in_(
                    {
                        "Expense approvals pending",
                        "Expense claims missing approver assignment",
                    }
                ),
            )
        )

        # Fast-path: if MetricStore says zero pending approvals, skip everything.
        if self._quick_check_from_store(organization_id):
            return 0

        # Org-wide: missing approver assignment for pending approvals.
        missing_cnt = int(
            self.db.scalar(
                select(func.count())
                .select_from(ExpenseClaim)
                .where(
                    ExpenseClaim.organization_id == organization_id,
                    ExpenseClaim.status == ExpenseClaimStatus.PENDING_APPROVAL,
                    ExpenseClaim.approver_id.is_(None),
                )
            )
            or 0
        )
        missing = self.generate_missing_approver_insight(organization_id, missing_cnt)
        if missing:
            self.db.add(missing)
            written += 1

        for approver_id, _, _ in self.top_pending_approvers(
            organization_id, limit=limit
        ):
            summary = self.pending_approvals_for_approver(organization_id, approver_id)
            insight = self.generate_approver_backlog_insight(
                organization_id, approver_id, summary
            )
            if not insight:
                continue
            self.db.add(insight)
            written += 1

        self.db.flush()
        return written
