"""Published schedule resolution for attendance and downstream modules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from uuid import UUID

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.orm import Session, aliased, joinedload

from app.models.people.scheduling import ScheduleStatus, ShiftSchedule, WorkSchedule

_PUBLISHED_STATUSES = (ScheduleStatus.PUBLISHED, ScheduleStatus.COMPLETED)


@dataclass(frozen=True)
class ResolvedShift:
    assignment: ShiftSchedule
    scheduled_start: datetime
    scheduled_end: datetime

    @classmethod
    def from_assignment(
        cls, assignment: ShiftSchedule, timezone: tzinfo | None
    ) -> ResolvedShift:
        shift = assignment.shift_type
        start = datetime.combine(assignment.shift_date, shift.start_time, timezone)
        end = datetime.combine(assignment.shift_date, shift.end_time, timezone)
        if shift.end_time <= shift.start_time:
            end += timedelta(days=1)
        return cls(assignment=assignment, scheduled_start=start, scheduled_end=end)


class ScheduleResolver:
    """Read the published roster used by attendance."""

    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _publication_conditions(
        organization_id: UUID,
    ) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
        revision = aliased(WorkSchedule)
        latest_revision = (
            select(func.max(revision.revision))
            .where(
                revision.organization_id == organization_id,
                revision.department_id == WorkSchedule.department_id,
                revision.period_start == WorkSchedule.period_start,
                revision.period_end == WorkSchedule.period_end,
                revision.status.in_(_PUBLISHED_STATUSES),
            )
            .correlate(WorkSchedule)
            .scalar_subquery()
        )
        standalone = ShiftSchedule.work_schedule_id.is_(None)
        published = and_(
            ShiftSchedule.organization_id == organization_id,
            ShiftSchedule.status.in_(_PUBLISHED_STATUSES),
            or_(
                standalone,
                and_(
                    WorkSchedule.organization_id == organization_id,
                    WorkSchedule.status.in_(_PUBLISHED_STATUSES),
                ),
            ),
        )
        effective = or_(standalone, WorkSchedule.revision == latest_revision)
        return published, effective

    def get_published_shift_dates(
        self,
        *,
        organization_id: UUID,
        employee_id: UUID,
        start_date: date,
        end_date: date,
    ) -> frozenset[date] | None:
        """Return published workdays, or None when there is no employee roster."""
        published, effective = self._publication_conditions(organization_id)
        rows = self.db.execute(
            select(ShiftSchedule.shift_date, effective)
            .outerjoin(
                WorkSchedule,
                WorkSchedule.work_schedule_id == ShiftSchedule.work_schedule_id,
            )
            .where(
                published,
                ShiftSchedule.employee_id == employee_id,
                or_(
                    and_(
                        ShiftSchedule.work_schedule_id.is_(None),
                        ShiftSchedule.shift_date.between(start_date, end_date),
                    ),
                    and_(
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

    def list_effective_assignments(
        self,
        *,
        organization_id: UUID,
        start_date: date,
        end_date: date,
        employee_id: UUID | None = None,
    ) -> list[ShiftSchedule]:
        """Return effective assignments, highest workspace revision first."""
        published, effective = self._publication_conditions(organization_id)
        query = (
            select(ShiftSchedule)
            .outerjoin(
                WorkSchedule,
                WorkSchedule.work_schedule_id == ShiftSchedule.work_schedule_id,
            )
            .options(joinedload(ShiftSchedule.shift_type))
            .where(
                published,
                effective,
                ShiftSchedule.shift_date.between(start_date, end_date),
            )
            .order_by(
                WorkSchedule.revision.desc().nulls_last(),
                ShiftSchedule.revision.desc(),
                ShiftSchedule.shift_date.desc(),
            )
        )
        if employee_id is not None:
            query = query.where(ShiftSchedule.employee_id == employee_id)
        return list(self.db.scalars(query).all())

    def get_effective_assignment(
        self,
        *,
        organization_id: UUID,
        employee_id: UUID,
        shift_date: date,
    ) -> ShiftSchedule | None:
        assignments = self.list_effective_assignments(
            organization_id=organization_id,
            employee_id=employee_id,
            start_date=shift_date,
            end_date=shift_date,
        )
        return next(iter(assignments), None)

    def resolve_employee_shift(
        self,
        organization_id: UUID,
        employee_id: UUID,
        timestamp: datetime,
    ) -> ResolvedShift | None:
        local_date = timestamp.date()
        assignments = self.list_effective_assignments(
            organization_id=organization_id,
            employee_id=employee_id,
            start_date=local_date - timedelta(days=1),
            end_date=local_date,
        )
        windows = [
            ResolvedShift.from_assignment(assignment, timestamp.tzinfo)
            for assignment in assignments
        ]
        # An active shift takes precedence over another shift's punch window.
        for shift in windows:
            if shift.scheduled_start <= timestamp <= shift.scheduled_end:
                return shift
        for shift in windows:
            if shift.scheduled_end.date() < local_date:
                continue
            if (
                shift.scheduled_start - timedelta(hours=6)
                <= timestamp
                <= shift.scheduled_end + timedelta(hours=6)
            ):
                return shift
        return None
