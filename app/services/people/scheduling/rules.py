"""Data-driven scheduling rule evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.people.leave import LeaveApplication, LeaveApplicationStatus
from app.models.people.scheduling import SchedulingPolicy, ShiftSchedule, WorkSchedule


@dataclass(frozen=True)
class ScheduleRuleIssue:
    rule_key: str
    severity: str
    message: str
    employee_id: UUID | None = None
    required: str | None = None
    actual: str | None = None


@dataclass(frozen=True)
class ScheduleRuleResult:
    valid: bool
    errors: list[ScheduleRuleIssue]
    warnings: list[ScheduleRuleIssue]


class ScheduleRuleEvaluator:
    """Evaluate scheduler policies without putting business rules in routes/UI."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def evaluate(self, organization_id: UUID, schedule: WorkSchedule) -> ScheduleRuleResult:
        policies = self._policies(organization_id, schedule.department_id)
        assignments = list(
            self.db.scalars(
                select(ShiftSchedule)
                .options(joinedload(ShiftSchedule.shift_type))
                .where(
                    ShiftSchedule.organization_id == organization_id,
                    ShiftSchedule.work_schedule_id == schedule.work_schedule_id,
                )
                .order_by(ShiftSchedule.employee_id, ShiftSchedule.shift_date)
            ).unique().all()
        )
        issues: list[ScheduleRuleIssue] = []
        issues.extend(self._overlap_issues(policies, assignments))
        issues.extend(self._max_hours_issues(policies, assignments))
        issues.extend(self._min_rest_issues(policies, assignments))
        issues.extend(self._leave_issues(organization_id, policies, assignments))

        errors = [issue for issue in issues if issue.severity == "error"]
        warnings = [issue for issue in issues if issue.severity != "error"]
        return ScheduleRuleResult(valid=not errors, errors=errors, warnings=warnings)

    def _policies(self, organization_id: UUID, department_id: UUID) -> dict[str, SchedulingPolicy]:
        rows = list(
            self.db.scalars(
                select(SchedulingPolicy).where(
                    SchedulingPolicy.organization_id == organization_id,
                    SchedulingPolicy.enabled.is_(True),
                    or_(
                        SchedulingPolicy.department_id.is_(None),
                        SchedulingPolicy.department_id == department_id,
                    ),
                )
            ).all()
        )
        policies: dict[str, SchedulingPolicy] = {}
        for row in rows:
            current = policies.get(row.rule_key)
            if current is None or row.department_id is not None:
                policies[row.rule_key] = row
        return policies

    @staticmethod
    def _duration_hours(item: ShiftSchedule) -> Decimal:
        start = datetime.combine(item.shift_date, item.shift_type.start_time)
        end = datetime.combine(item.shift_date, item.shift_type.end_time)
        if item.shift_type.end_time <= item.shift_type.start_time:
            end += timedelta(days=1)
        hours = Decimal(str((end - start).total_seconds() / 3600))
        break_hours = Decimal(str(item.shift_type.break_duration_minutes or 0)) / Decimal("60")
        return max(Decimal("0"), hours - break_hours)

    def _overlap_issues(self, policies: dict[str, SchedulingPolicy], assignments: list[ShiftSchedule]) -> list[ScheduleRuleIssue]:
        policy = policies.get("overlapping_shifts_allowed")
        if policy and bool(policy.configuration.get("allowed", False)):
            return []
        severity = policy.severity if policy else "error"
        seen: set[tuple[UUID, object]] = set()
        issues: list[ScheduleRuleIssue] = []
        for item in assignments:
            key = (item.employee_id, item.shift_date)
            if key in seen:
                issues.append(ScheduleRuleIssue("overlapping_shifts_allowed", severity, "Employee has more than one shift on the same date", item.employee_id))
            seen.add(key)
        return issues

    def _max_hours_issues(self, policies: dict[str, SchedulingPolicy], assignments: list[ShiftSchedule]) -> list[ScheduleRuleIssue]:
        policy = policies.get("maximum_hours_per_week")
        if not policy:
            return []
        maximum = Decimal(str(policy.configuration.get("hours", "0") or "0"))
        if maximum <= 0:
            return []
        totals: dict[UUID, Decimal] = {}
        for item in assignments:
            totals[item.employee_id] = totals.get(item.employee_id, Decimal("0")) + self._duration_hours(item)
        return [
            ScheduleRuleIssue("maximum_hours_per_week", policy.severity, "Employee exceeds maximum weekly scheduled hours", employee_id, f"{maximum}h", f"{actual}h")
            for employee_id, actual in totals.items()
            if actual > maximum
        ]

    def _min_rest_issues(self, policies: dict[str, SchedulingPolicy], assignments: list[ShiftSchedule]) -> list[ScheduleRuleIssue]:
        policy = policies.get("minimum_rest_hours")
        if not policy:
            return []
        required = Decimal(str(policy.configuration.get("hours", "0") or "0"))
        if required <= 0:
            return []
        grouped: dict[UUID, list[ShiftSchedule]] = {}
        for item in assignments:
            grouped.setdefault(item.employee_id, []).append(item)
        issues: list[ScheduleRuleIssue] = []
        for employee_id, rows in grouped.items():
            previous_end: datetime | None = None
            for item in sorted(rows, key=lambda row: (row.shift_date, row.shift_type.start_time)):
                start = datetime.combine(item.shift_date, item.shift_type.start_time)
                end = datetime.combine(item.shift_date, item.shift_type.end_time)
                if item.shift_type.end_time <= item.shift_type.start_time:
                    end += timedelta(days=1)
                if previous_end:
                    rest = Decimal(str((start - previous_end).total_seconds() / 3600))
                    if rest < required:
                        issues.append(ScheduleRuleIssue("minimum_rest_hours", policy.severity, "Employee has insufficient rest between shifts", employee_id, f"{required}h", f"{rest}h"))
                previous_end = end
        return issues

    def _leave_issues(self, organization_id: UUID, policies: dict[str, SchedulingPolicy], assignments: list[ShiftSchedule]) -> list[ScheduleRuleIssue]:
        policy = policies.get("schedule_approved_leave")
        if policy and bool(policy.configuration.get("allowed", False)):
            return []
        severity = policy.severity if policy else "error"
        issues: list[ScheduleRuleIssue] = []
        for item in assignments:
            leave = self.db.scalar(
                select(LeaveApplication.application_id).where(
                    LeaveApplication.organization_id == organization_id,
                    LeaveApplication.employee_id == item.employee_id,
                    LeaveApplication.status == LeaveApplicationStatus.APPROVED,
                    LeaveApplication.from_date <= item.shift_date,
                    LeaveApplication.to_date >= item.shift_date,
                ).limit(1)
            )
            if leave:
                issues.append(ScheduleRuleIssue("schedule_approved_leave", severity, "Employee is on approved leave for this shift", item.employee_id))
        return issues
