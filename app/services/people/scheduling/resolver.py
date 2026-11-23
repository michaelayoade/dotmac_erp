"""Published schedule resolution for attendance and downstream modules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased, joinedload

from app.models.people.scheduling import ScheduleStatus, ShiftSchedule, WorkSchedule


@dataclass(frozen=True)
class ResolvedShift:
    schedule: WorkSchedule
    assignment: ShiftSchedule
    scheduled_start: datetime
    scheduled_end: datetime


class ScheduleResolver:
    """Resolve the expected published shift for an employee at a timestamp."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_published_shift_dates(
        self,
        *,
        organization_id: UUID,
        employee_id: UUID,
        start_date: date,
        end_date: date,
    ) -> frozenset[date] | None:
        """Return published workdays, or None when there is no employee roster."""
        published = (ScheduleStatus.PUBLISHED, ScheduleStatus.COMPLETED)
        revision = aliased(WorkSchedule)
        latest_revision = (
            select(func.max(revision.revision))
            .where(
                revision.organization_id == organization_id,
                revision.department_id == WorkSchedule.department_id,
                revision.period_start == WorkSchedule.period_start,
                revision.period_end == WorkSchedule.period_end,
                revision.status.in_(published),
            )
            .correlate(WorkSchedule)
            .scalar_subquery()
        )
        rows = self.db.execute(
            select(
                ShiftSchedule.shift_date,
                or_(
                    ShiftSchedule.work_schedule_id.is_(None),
                    WorkSchedule.revision == latest_revision,
                ),
            )
            .outerjoin(
                WorkSchedule,
                WorkSchedule.work_schedule_id == ShiftSchedule.work_schedule_id,
            )
            .where(
                ShiftSchedule.organization_id == organization_id,
                ShiftSchedule.employee_id == employee_id,
                ShiftSchedule.status.in_(published),
                or_(
                    and_(
                        ShiftSchedule.work_schedule_id.is_(None),
                        ShiftSchedule.shift_date.between(start_date, end_date),
                    ),
                    and_(
                        WorkSchedule.organization_id == organization_id,
                        WorkSchedule.status.in_(published),
                        WorkSchedule.period_start <= end_date,
                        WorkSchedule.period_end >= start_date,
                    ),
                ),
            )
        ).all()
        if not rows:
            return None
        # Prior revisions establish roster membership even if the latest
        # publication removed every shift for this employee.
        return frozenset(
            shift_date
            for shift_date, is_effective in rows
            if is_effective and start_date <= shift_date <= end_date
        )

    def resolve_employee_shift(
        self,
        organization_id: UUID,
        employee_id: UUID,
        timestamp: datetime,
    ) -> ResolvedShift | None:
        local_date = timestamp.date()
        candidates = list(
            self.db.scalars(
                select(ShiftSchedule)
                .join(
                    WorkSchedule,
                    WorkSchedule.work_schedule_id == ShiftSchedule.work_schedule_id,
                )
                .options(
                    joinedload(ShiftSchedule.shift_type),
                    joinedload(ShiftSchedule.department),
                )
                .where(
                    ShiftSchedule.organization_id == organization_id,
                    ShiftSchedule.employee_id == employee_id,
                    ShiftSchedule.shift_date.in_(
                        [local_date, local_date - timedelta(days=1)]
                    ),
                    ShiftSchedule.status == ScheduleStatus.PUBLISHED,
                    WorkSchedule.organization_id == organization_id,
                    WorkSchedule.status == ScheduleStatus.PUBLISHED,
                )
                .order_by(WorkSchedule.revision.desc(), ShiftSchedule.revision.desc())
            )
            .unique()
            .all()
        )
        for assignment in candidates:
            shift = assignment.shift_type
            scheduled_start = datetime.combine(assignment.shift_date, shift.start_time)
            scheduled_end = datetime.combine(assignment.shift_date, shift.end_time)
            if timestamp.tzinfo is not None:
                scheduled_start = scheduled_start.replace(tzinfo=timestamp.tzinfo)
                scheduled_end = scheduled_end.replace(tzinfo=timestamp.tzinfo)
            if shift.end_time <= shift.start_time:
                scheduled_end += timedelta(days=1)
            if (
                scheduled_start - timedelta(hours=6)
                <= timestamp
                <= scheduled_end + timedelta(hours=6)
            ):
                schedule = self.db.get(WorkSchedule, assignment.work_schedule_id)
                if schedule is None:
                    continue
                return ResolvedShift(
                    schedule=schedule,
                    assignment=assignment,
                    scheduled_start=scheduled_start,
                    scheduled_end=scheduled_end,
                )
        return None
