"""Operational efficiency analyzer (deterministic, no LLM required).

Monitors cross-entity approval bottlenecks, workflow automation health,
and period-close velocity.  Complements the banking and expense analyzers
which handle reconciliation freshness and expense-specific backlogs.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.coach.insight import CoachInsight
from app.models.finance.automation.workflow_execution import (
    ExecutionStatus,
    WorkflowExecution,
)
from app.models.finance.automation.workflow_rule import WorkflowRule
from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.models.people.leave.leave_application import (
    LeaveApplication,
    LeaveApplicationStatus,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PeriodCloseVelocitySummary:
    """How quickly the org closes fiscal periods after they end."""

    avg_close_lag_days: Decimal | None  # avg days between end_date and closed_at
    periods_closed_90d: int  # number of periods closed in last 90 days
    periods_still_open_past_end: int  # open periods whose end_date has passed


@dataclass(frozen=True)
class LeaveApprovalBacklogSummary:
    """Leave requests awaiting approval, by approver."""

    total_pending: int
    oldest_pending_days: int
    top_approvers: list[dict]  # [{employee_id, name, pending_count}]


@dataclass(frozen=True)
class WorkflowHealthSummary:
    """Automation rule execution health over the last 30 days."""

    total_executions: int
    success_count: int
    failure_count: int
    success_rate_pct: Decimal
    failing_rules: list[dict]  # [{rule_id, rule_name, failure_count}]


# ---------------------------------------------------------------------------
# Severity helpers
# ---------------------------------------------------------------------------


def _severity_for_period_close(avg_lag: Decimal | None, open_past_end: int) -> str:
    if open_past_end >= 3:
        return "WARNING"
    if avg_lag is not None and avg_lag > 15:
        return "ATTENTION"
    if open_past_end >= 1:
        return "ATTENTION"
    return "INFO"


def _severity_for_leave_backlog(total_pending: int, oldest_days: int) -> str:
    if oldest_days >= 14:
        return "WARNING"
    if total_pending >= 10 or oldest_days >= 7:
        return "ATTENTION"
    return "INFO"


def _severity_for_workflow_health(success_rate: Decimal, failure_count: int) -> str:
    if success_rate < 80 or failure_count >= 20:
        return "WARNING"
    if success_rate < 90 or failure_count >= 5:
        return "ATTENTION"
    return "INFO"


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class EfficiencyAnalyzer:
    """Deterministic operational efficiency analyzer.

    Generates org-wide Finance/Operations insights for:
    - Fiscal period close velocity
    - Leave approval bottlenecks
    - Workflow automation health
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # MetricStore fast-path
    # ------------------------------------------------------------------
    def _quick_check_from_store(self, organization_id: UUID) -> bool:
        """Return True if MetricStore shows zero efficiency issues."""
        from app.services.coach.analyzers import metric_is_fresh

        fresh, value = metric_is_fresh(
            self.db, organization_id, "efficiency.pending_expense_approvals"
        )
        if fresh and value is not None and value <= 0:
            # Also check unreconciled accounts
            fresh2, val2 = metric_is_fresh(
                self.db, organization_id, "efficiency.unreconciled_account_count"
            )
            if fresh2 and (val2 or Decimal("0")) <= 0:
                logger.debug("Efficiency fast-path: zero pending + zero unreconciled")
                return True
        return False

    # ------------------------------------------------------------------
    # Core computations
    # ------------------------------------------------------------------
    def period_close_velocity(
        self, organization_id: UUID
    ) -> PeriodCloseVelocitySummary:
        """Measure how quickly fiscal periods are closed after their end date."""
        today = date.today()
        cutoff_90d = today - timedelta(days=90)

        # Periods closed in last 90 days: avg lag between end_date and closed_at
        closed_stmt = (
            select(
                func.count().label("cnt"),
                func.avg(
                    func.extract(
                        "day",
                        func.age(FiscalPeriod.hard_closed_at, FiscalPeriod.end_date),
                    )
                ).label("avg_lag"),
            )
            .select_from(FiscalPeriod)
            .where(
                FiscalPeriod.organization_id == organization_id,
                FiscalPeriod.status == PeriodStatus.HARD_CLOSED,
                FiscalPeriod.hard_closed_at.is_not(None),
                FiscalPeriod.hard_closed_at >= cutoff_90d,
            )
        )
        row = self.db.execute(closed_stmt).one()
        periods_closed = int(row.cnt or 0)
        avg_lag = (
            Decimal(str(round(float(row.avg_lag), 1)))
            if row.avg_lag is not None
            else None
        )

        # Open periods past their end date
        open_past_stmt = (
            select(func.count())
            .select_from(FiscalPeriod)
            .where(
                FiscalPeriod.organization_id == organization_id,
                FiscalPeriod.status.in_((PeriodStatus.OPEN, PeriodStatus.REOPENED)),
                FiscalPeriod.end_date < today,
            )
        )
        open_past_end = int(self.db.scalar(open_past_stmt) or 0)

        return PeriodCloseVelocitySummary(
            avg_close_lag_days=avg_lag,
            periods_closed_90d=periods_closed,
            periods_still_open_past_end=open_past_end,
        )

    def leave_approval_backlog(
        self, organization_id: UUID
    ) -> LeaveApprovalBacklogSummary:
        """Measure pending leave request backlog."""
        from app.models.people.hr.employee import Employee

        # Total pending
        total_stmt = (
            select(func.count())
            .select_from(LeaveApplication)
            .where(
                LeaveApplication.organization_id == organization_id,
                LeaveApplication.status == LeaveApplicationStatus.SUBMITTED,
            )
        )
        total_pending = int(self.db.scalar(total_stmt) or 0)

        if total_pending == 0:
            return LeaveApprovalBacklogSummary(
                total_pending=0, oldest_pending_days=0, top_approvers=[]
            )

        # Oldest pending
        oldest_stmt = (
            select(func.min(LeaveApplication.created_at))
            .select_from(LeaveApplication)
            .where(
                LeaveApplication.organization_id == organization_id,
                LeaveApplication.status == LeaveApplicationStatus.SUBMITTED,
            )
        )
        oldest_raw = self.db.scalar(oldest_stmt)
        oldest_days = 0
        if oldest_raw:
            oldest_dt = oldest_raw
            if oldest_dt.tzinfo is None:
                oldest_dt = oldest_dt.replace(tzinfo=UTC)
            oldest_days = max((datetime.now(UTC) - oldest_dt).days, 0)

        # Top approvers by pending count (leave_approver_id)
        approver_stmt = (
            select(
                LeaveApplication.leave_approver_id,
                func.count().label("cnt"),
            )
            .where(
                LeaveApplication.organization_id == organization_id,
                LeaveApplication.status == LeaveApplicationStatus.SUBMITTED,
                LeaveApplication.leave_approver_id.is_not(None),
            )
            .group_by(LeaveApplication.leave_approver_id)
            .order_by(func.count().desc())
            .limit(5)
        )
        approver_rows = self.db.execute(approver_stmt).all()

        top_approvers: list[dict] = []
        for approver_id, cnt in approver_rows:
            emp = self.db.get(Employee, approver_id)
            name = f"{emp.first_name} {emp.last_name}" if emp else str(approver_id)
            top_approvers.append(
                {
                    "employee_id": str(approver_id),
                    "name": name,
                    "pending_count": int(cnt),
                }
            )

        return LeaveApprovalBacklogSummary(
            total_pending=total_pending,
            oldest_pending_days=oldest_days,
            top_approvers=top_approvers,
        )

    def workflow_health(self, organization_id: UUID) -> WorkflowHealthSummary:
        """Measure workflow automation health over the last 30 days."""
        cutoff_30d = datetime.now(UTC) - timedelta(days=30)

        # Total executions
        total_stmt = (
            select(func.count())
            .select_from(WorkflowExecution)
            .join(
                WorkflowRule,
                WorkflowRule.rule_id == WorkflowExecution.rule_id,
            )
            .where(
                WorkflowRule.organization_id == organization_id,
                WorkflowExecution.triggered_at >= cutoff_30d,
            )
        )
        total = int(self.db.scalar(total_stmt) or 0)

        if total == 0:
            return WorkflowHealthSummary(
                total_executions=0,
                success_count=0,
                failure_count=0,
                success_rate_pct=Decimal("100"),
                failing_rules=[],
            )

        # Success count
        success_stmt = (
            select(func.count())
            .select_from(WorkflowExecution)
            .join(WorkflowRule, WorkflowRule.rule_id == WorkflowExecution.rule_id)
            .where(
                WorkflowRule.organization_id == organization_id,
                WorkflowExecution.triggered_at >= cutoff_30d,
                WorkflowExecution.status == ExecutionStatus.SUCCESS,
            )
        )
        success = int(self.db.scalar(success_stmt) or 0)
        failure = total - success
        success_rate = (
            Decimal(str(round(success / total * 100, 1)))
            if total > 0
            else Decimal("100")
        )

        # Top failing rules
        failing_stmt = (
            select(
                WorkflowExecution.rule_id,
                WorkflowRule.rule_name.label("rule_name"),
                func.count().label("fail_cnt"),
            )
            .join(WorkflowRule, WorkflowRule.rule_id == WorkflowExecution.rule_id)
            .where(
                WorkflowRule.organization_id == organization_id,
                WorkflowExecution.triggered_at >= cutoff_30d,
                WorkflowExecution.status == ExecutionStatus.FAILED,
            )
            .group_by(WorkflowExecution.rule_id, WorkflowRule.rule_name)
            .order_by(func.count().desc())
            .limit(5)
        )
        failing_rows = self.db.execute(failing_stmt).all()
        failing_rules: list[dict] = [
            {
                "rule_id": str(r.rule_id),
                "rule_name": str(r.rule_name),
                "failure_count": int(r.fail_cnt),
            }
            for r in failing_rows
        ]

        return WorkflowHealthSummary(
            total_executions=total,
            success_count=success,
            failure_count=failure,
            success_rate_pct=success_rate,
            failing_rules=failing_rules,
        )

    # ------------------------------------------------------------------
    # Insight generation
    # ------------------------------------------------------------------
    def generate_period_close_insight(
        self, organization_id: UUID
    ) -> CoachInsight | None:
        velocity = self.period_close_velocity(organization_id)
        if velocity.periods_still_open_past_end == 0 and (
            velocity.avg_close_lag_days is None or velocity.avg_close_lag_days <= 7
        ):
            return None

        severity = _severity_for_period_close(
            velocity.avg_close_lag_days,
            velocity.periods_still_open_past_end,
        )

        summary_parts: list[str] = []
        if velocity.periods_still_open_past_end > 0:
            summary_parts.append(
                f"{velocity.periods_still_open_past_end} fiscal period(s) "
                "are past their end date but still open."
            )
        if velocity.avg_close_lag_days is not None:
            summary_parts.append(
                f"Average close lag (last 90d): {velocity.avg_close_lag_days} day(s)."
            )
        summary_text = " ".join(summary_parts)

        return CoachInsight(
            insight_id=uuid.uuid4(),
            organization_id=organization_id,
            audience="FINANCE",
            target_employee_id=None,
            category="EFFICIENCY",
            severity=severity,
            title="Period close velocity",
            summary=summary_text,
            detail=None,
            coaching_action=(
                "Aim to close fiscal periods within 5-7 days of end date. "
                "Identify bottlenecks in the close checklist — typically "
                "outstanding journal entries or pending reconciliations."
            ),
            confidence=0.9,
            data_sources={"gl.fiscal_period": velocity.periods_closed_90d},
            evidence={
                "avg_close_lag_days": (
                    str(velocity.avg_close_lag_days)
                    if velocity.avg_close_lag_days is not None
                    else None
                ),
                "periods_closed_90d": velocity.periods_closed_90d,
                "periods_still_open_past_end": velocity.periods_still_open_past_end,
            },
            status="GENERATED",
            delivered_at=None,
            read_at=None,
            dismissed_at=None,
            feedback=None,
            valid_until=date.today() + timedelta(days=1),
            created_at=datetime.now(UTC),
        )

    def generate_leave_backlog_insight(
        self, organization_id: UUID
    ) -> CoachInsight | None:
        backlog = self.leave_approval_backlog(organization_id)
        if backlog.total_pending == 0:
            return None

        severity = _severity_for_leave_backlog(
            backlog.total_pending, backlog.oldest_pending_days
        )

        summary_text = (
            f"{backlog.total_pending} leave request(s) awaiting approval. "
            f"Oldest: {backlog.oldest_pending_days} day(s)."
        )
        if backlog.top_approvers:
            top = backlog.top_approvers[0]
            summary_text += f" Top approver backlog: {top['name']} ({top['pending_count']} pending)."

        return CoachInsight(
            insight_id=uuid.uuid4(),
            organization_id=organization_id,
            audience="HR",
            target_employee_id=None,
            category="EFFICIENCY",
            severity=severity,
            title="Leave approval backlog",
            summary=summary_text,
            detail=None,
            coaching_action=(
                "Clear pending leave requests promptly — delayed approvals "
                "cause employee frustration and last-minute scheduling issues. "
                "If specific approvers are bottlenecks, consider adding "
                "delegate approvers."
            ),
            confidence=0.9,
            data_sources={"leave.leave_application": backlog.total_pending},
            evidence={
                "total_pending": backlog.total_pending,
                "oldest_pending_days": backlog.oldest_pending_days,
                "top_approvers": backlog.top_approvers,
            },
            status="GENERATED",
            delivered_at=None,
            read_at=None,
            dismissed_at=None,
            feedback=None,
            valid_until=date.today() + timedelta(days=1),
            created_at=datetime.now(UTC),
        )

    def generate_workflow_health_insight(
        self, organization_id: UUID
    ) -> CoachInsight | None:
        health = self.workflow_health(organization_id)
        if health.total_executions == 0:
            return None
        if health.success_rate_pct >= 95 and health.failure_count == 0:
            return None

        severity = _severity_for_workflow_health(
            health.success_rate_pct, health.failure_count
        )

        summary_text = (
            f"Workflow automation: {health.total_executions} executions in 30 days. "
            f"Success rate: {health.success_rate_pct}% "
            f"({health.failure_count} failure(s))."
        )
        if health.failing_rules:
            top_rule = health.failing_rules[0]
            summary_text += (
                f" Top failing rule: '{top_rule['rule_name']}' "
                f"({top_rule['failure_count']} failures)."
            )

        return CoachInsight(
            insight_id=uuid.uuid4(),
            organization_id=organization_id,
            audience="FINANCE",
            target_employee_id=None,
            category="EFFICIENCY",
            severity=severity,
            title="Workflow automation health",
            summary=summary_text,
            detail=None,
            coaching_action=(
                "Review failing workflow rules — check trigger conditions and "
                "action configurations. Rules with >10% failure rate likely have "
                "misconfigured conditions. Disable broken rules to avoid noisy errors."
            ),
            confidence=0.85,
            data_sources={
                "automation.workflow_execution": health.total_executions,
            },
            evidence={
                "total_executions": health.total_executions,
                "success_count": health.success_count,
                "failure_count": health.failure_count,
                "success_rate_pct": str(health.success_rate_pct),
                "failing_rules": health.failing_rules,
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
        written = 0

        # Delete today's efficiency insights (excluding banking/expense which
        # have their own analyzers)
        self.db.execute(
            delete(CoachInsight).where(
                CoachInsight.organization_id == organization_id,
                CoachInsight.target_employee_id.is_(None),
                CoachInsight.category == "EFFICIENCY",
                func.date(CoachInsight.created_at) == today,
                CoachInsight.title.in_(
                    [
                        "Period close velocity",
                        "Leave approval backlog",
                        "Workflow automation health",
                    ]
                ),
            )
        )

        for gen in (
            self.generate_period_close_insight,
            self.generate_leave_backlog_insight,
            self.generate_workflow_health_insight,
        ):
            insight = gen(organization_id)
            if insight:
                self.db.add(insight)
                written += 1

        if written:
            self.db.flush()
        return written
