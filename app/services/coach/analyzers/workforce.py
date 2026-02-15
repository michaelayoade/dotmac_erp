"""Workforce health analyzer (deterministic, no LLM required).

Monitors attrition risk, leave utilization, and department staffing gaps.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session

from app.models.coach.insight import CoachInsight
from app.models.people.hr.department import Department
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.leave.leave_application import (
    LeaveApplication,
    LeaveApplicationStatus,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkforceHealthSummary:
    active_headcount: int
    recent_departures_90d: int
    annualized_turnover_pct: Decimal
    departments_without_head: int
    employees_without_manager: int


@dataclass(frozen=True)
class LeaveUtilizationSummary:
    pending_leave_requests: int
    avg_approval_days: Decimal | None  # avg days from submission to approval
    leave_days_used_90d: Decimal


def _severity_for_workforce(turnover_pct: Decimal, depts_no_head: int) -> str:
    if turnover_pct > 20:
        return "WARNING"
    if turnover_pct > 10 or depts_no_head >= 2:
        return "ATTENTION"
    return "INFO"


def _severity_for_leave(pending: int) -> str:
    if pending >= 10:
        return "WARNING"
    if pending >= 5:
        return "ATTENTION"
    return "INFO"


class WorkforceAnalyzer:
    """Deterministic workforce health analyzer.

    Generates org-wide HR insights for turnover risk, staffing gaps,
    and leave utilization patterns.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # MetricStore fast-path
    # ------------------------------------------------------------------
    def _quick_check_from_store(self, organization_id: UUID) -> bool:
        """Return True if MetricStore shows zero active employees."""
        from app.services.coach.analyzers import metric_is_fresh

        fresh, value = metric_is_fresh(
            self.db, organization_id, "workforce.active_headcount"
        )
        if fresh and value is not None and value <= 0:
            logger.debug("Workforce fast-path: zero active employees, skipping")
            return True
        return False

    # ------------------------------------------------------------------
    # Core computations
    # ------------------------------------------------------------------
    def workforce_health(self, organization_id: UUID) -> WorkforceHealthSummary:
        active_base = and_(
            Employee.organization_id == organization_id,
            Employee.status == EmployeeStatus.ACTIVE,
        )

        active_count = int(
            self.db.scalar(
                select(func.count()).select_from(Employee).where(active_base)
            )
            or 0
        )

        # Departures in last 90 days
        cutoff_90d = date.today() - timedelta(days=90)
        departure_statuses = (
            EmployeeStatus.RESIGNED,
            EmployeeStatus.TERMINATED,
            EmployeeStatus.RETIRED,
        )
        departures = int(
            self.db.scalar(
                select(func.count())
                .select_from(Employee)
                .where(
                    Employee.organization_id == organization_id,
                    Employee.status.in_(departure_statuses),
                    Employee.date_of_leaving >= cutoff_90d,
                )
            )
            or 0
        )

        # Annualized turnover: (departures / 90 * 365) / headcount * 100
        if active_count > 0 and departures > 0:
            annual_departures = Decimal(str(departures)) / 90 * 365
            turnover_pct = round(
                annual_departures / Decimal(str(active_count)) * 100, 1
            )
        else:
            turnover_pct = Decimal("0")

        # Departments without a head
        depts_no_head = int(
            self.db.scalar(
                select(func.count())
                .select_from(Department)
                .where(
                    Department.organization_id == organization_id,
                    Department.is_active.is_(True),
                    Department.head_id.is_(None),
                )
            )
            or 0
        )

        # Employees without a reporting manager
        no_manager = int(
            self.db.scalar(
                select(func.count())
                .select_from(Employee)
                .where(active_base, Employee.reports_to_id.is_(None))
            )
            or 0
        )

        return WorkforceHealthSummary(
            active_headcount=active_count,
            recent_departures_90d=departures,
            annualized_turnover_pct=turnover_pct,
            departments_without_head=depts_no_head,
            employees_without_manager=no_manager,
        )

    def leave_utilization(self, organization_id: UUID) -> LeaveUtilizationSummary:
        # Pending leave requests
        pending = int(
            self.db.scalar(
                select(func.count())
                .select_from(LeaveApplication)
                .where(
                    LeaveApplication.organization_id == organization_id,
                    LeaveApplication.status == LeaveApplicationStatus.SUBMITTED,
                )
            )
            or 0
        )

        # Average approval time (submitted → approved) in last 90 days
        cutoff = date.today() - timedelta(days=90)
        avg_days_stmt = (
            select(
                func.avg(
                    func.extract(
                        "day",
                        func.age(
                            LeaveApplication.approved_at,
                            LeaveApplication.created_at,
                        ),
                    )
                )
            )
            .select_from(LeaveApplication)
            .where(
                LeaveApplication.organization_id == organization_id,
                LeaveApplication.status == LeaveApplicationStatus.APPROVED,
                LeaveApplication.approved_at.is_not(None),
                LeaveApplication.approved_at >= cutoff,
            )
        )
        avg_days_raw = self.db.scalar(avg_days_stmt)
        avg_approval_days = (
            round(Decimal(str(avg_days_raw)), 1) if avg_days_raw else None
        )

        # Leave days consumed in last 90 days
        used_stmt = (
            select(func.coalesce(func.sum(LeaveApplication.total_leave_days), 0))
            .select_from(LeaveApplication)
            .where(
                LeaveApplication.organization_id == organization_id,
                LeaveApplication.status == LeaveApplicationStatus.APPROVED,
                LeaveApplication.from_date >= cutoff,
            )
        )
        leave_days = Decimal(str(self.db.scalar(used_stmt) or "0"))

        return LeaveUtilizationSummary(
            pending_leave_requests=pending,
            avg_approval_days=avg_approval_days,
            leave_days_used_90d=leave_days,
        )

    # ------------------------------------------------------------------
    # Insight generation
    # ------------------------------------------------------------------
    def generate_workforce_health_insight(
        self, organization_id: UUID
    ) -> CoachInsight | None:
        if self._quick_check_from_store(organization_id):
            return None

        health = self.workforce_health(organization_id)
        if health.active_headcount == 0:
            return None

        severity = _severity_for_workforce(
            health.annualized_turnover_pct,
            health.departments_without_head,
        )

        # Only generate if there's something actionable
        if (
            severity == "INFO"
            and health.departments_without_head == 0
            and health.employees_without_manager == 0
        ):
            return None

        title = "Workforce health summary"
        summary_text = (
            f"Headcount: {health.active_headcount}. "
            f"90-day departures: {health.recent_departures_90d} "
            f"(annualized turnover: {health.annualized_turnover_pct}%). "
        )
        if health.departments_without_head > 0:
            summary_text += f"{health.departments_without_head} department(s) lack a designated head. "
        if health.employees_without_manager > 0:
            summary_text += f"{health.employees_without_manager} employee(s) have no reporting manager."

        coaching_action = (
            "Review departments without heads — assign interim leads to maintain "
            "approval workflows. If turnover exceeds 15%, investigate root causes "
            "(compensation, culture, management). Ensure all employees have a "
            "reporting manager for leave and expense approvals."
        )

        return CoachInsight(
            insight_id=uuid.uuid4(),
            organization_id=organization_id,
            audience="HR",
            target_employee_id=None,
            category="WORKFORCE",
            severity=severity,
            title=title,
            summary=summary_text,
            detail=None,
            coaching_action=coaching_action,
            confidence=0.9,
            data_sources={"hr.employee": health.active_headcount},
            evidence={
                "active_headcount": health.active_headcount,
                "recent_departures_90d": health.recent_departures_90d,
                "annualized_turnover_pct": str(health.annualized_turnover_pct),
                "departments_without_head": health.departments_without_head,
                "employees_without_manager": health.employees_without_manager,
            },
            status="GENERATED",
            delivered_at=None,
            read_at=None,
            dismissed_at=None,
            feedback=None,
            valid_until=date.today() + timedelta(days=1),
            created_at=datetime.now(UTC),
        )

    def generate_leave_utilization_insight(
        self, organization_id: UUID
    ) -> CoachInsight | None:
        if self._quick_check_from_store(organization_id):
            return None

        util = self.leave_utilization(organization_id)
        if util.pending_leave_requests == 0:
            return None

        severity = _severity_for_leave(util.pending_leave_requests)
        title = "Pending leave approvals backlog"

        summary_text = (
            f"{util.pending_leave_requests} leave request(s) awaiting approval. "
        )
        if util.avg_approval_days is not None:
            summary_text += f"Average approval time: {util.avg_approval_days} day(s). "
        summary_text += f"Total leave days consumed (90d): {util.leave_days_used_90d}."

        coaching_action = (
            "Clear the leave approval backlog to avoid employee dissatisfaction. "
            "If approvals consistently take more than 2 days, consider adding "
            "delegate approvers or adjusting the workflow."
        )

        return CoachInsight(
            insight_id=uuid.uuid4(),
            organization_id=organization_id,
            audience="HR",
            target_employee_id=None,
            category="WORKFORCE",
            severity=severity,
            title=title,
            summary=summary_text,
            detail=None,
            coaching_action=coaching_action,
            confidence=0.9,
            data_sources={"leave.leave_application": util.pending_leave_requests},
            evidence={
                "pending_leave_requests": util.pending_leave_requests,
                "avg_approval_days": (
                    str(util.avg_approval_days)
                    if util.avg_approval_days is not None
                    else None
                ),
                "leave_days_used_90d": str(util.leave_days_used_90d),
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

        # Workforce health
        self.db.execute(
            delete(CoachInsight).where(
                CoachInsight.organization_id == organization_id,
                CoachInsight.target_employee_id.is_(None),
                CoachInsight.category == "WORKFORCE",
                CoachInsight.audience == "HR",
                func.date(CoachInsight.created_at) == today,
                CoachInsight.title.in_(
                    ["Workforce health summary", "Pending leave approvals backlog"]
                ),
            )
        )

        for gen in (
            self.generate_workforce_health_insight,
            self.generate_leave_utilization_insight,
        ):
            insight = gen(organization_id)
            if insight:
                self.db.add(insight)
                written += 1

        if written:
            self.db.flush()
        return written
