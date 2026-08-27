"""Published schedule resolution for attendance and downstream modules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

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
