"""
Schedule Generator Service.

Handles monthly schedule generation from shift patterns.
"""

from __future__ import annotations

import logging
from calendar import monthrange
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.notification import EntityType, NotificationChannel, NotificationType
from app.models.people.leave import LeaveApplication, LeaveApplicationStatus
from app.models.people.scheduling import (
    RotationType,
    ScheduleStatus,
    ShiftPattern,
    ShiftPatternAssignment,
    ShiftSchedule,
)
from app.services.notification import NotificationService

logger = logging.getLogger(__name__)

# Singleton notification service
_notification_service = NotificationService()


# Mapping of day names to weekday integers (Monday=0)
DAY_TO_WEEKDAY = {
    "MON": 0,
    "TUE": 1,
    "WED": 2,
    "THU": 3,
    "FRI": 4,
    "SAT": 5,
    "SUN": 6,
}


class ScheduleGeneratorError(Exception):
    """Error during schedule generation."""

    pass


class ScheduleGenerator:
    """
    Schedule Generator - creates monthly shift schedules from patterns.

    Workflow:
    1. Get all active pattern assignments for department
    2. For each work day in the month:
       - Check if employee has approved leave
       - Determine shift type based on rotation
       - Create ShiftSchedule entry (DRAFT status)
    3. Return generation statistics
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def generate_monthly_schedule(
        self,
        org_id: UUID,
        department_id: UUID,
        year_month: str,
        created_by_id: UUID | None = None,
    ) -> dict:
        """
        Generate shift schedules for a department for a given month.

        Args:
            org_id: Organization ID
            department_id: Department to generate schedules for
            year_month: Month in YYYY-MM format
            created_by_id: Optional employee ID of the creator

        Returns:
            Dict with generation statistics
        """
        # Parse year_month
        try:
            year, month = map(int, year_month.split("-"))
        except ValueError:
            raise ScheduleGeneratorError(
                f"Invalid year_month format: {year_month}. Expected YYYY-MM"
            )

        # Check if schedules already exist for this month
        existing = self.db.scalar(
            select(ShiftSchedule.shift_schedule_id)
            .where(
                ShiftSchedule.organization_id == org_id,
                ShiftSchedule.department_id == department_id,
                ShiftSchedule.schedule_month == year_month,
            )
            .limit(1)
        )
        if existing:
            raise ScheduleGeneratorError(
                f"Schedules already exist for {year_month} in this department. "
                "Delete existing schedules first or generate for a different month."
            )

        # Get all active pattern assignments for this department
        assignments = self._get_active_assignments(org_id, department_id, year, month)

        if not assignments:
            logger.warning(
                "No active pattern assignments found for department %s",
                department_id,
            )
            return {
                "year_month": year_month,
                "department_id": str(department_id),
                "schedules_created": 0,
                "employees_scheduled": 0,
                "skipped_on_leave": 0,
            }

        # Get all approved leave for the month
        leave_dates = self._get_leave_dates_for_month(
            org_id, [a.employee_id for a in assignments], year, month
        )

        # Generate schedules
        schedules_created = 0
        skipped_on_leave = 0
        employee_ids_scheduled: set[UUID] = set()

        # Get month date range
        _, days_in_month = monthrange(year, month)
        month_start = date(year, month, 1)

        for assignment in assignments:
            pattern = assignment.shift_pattern
            employee_leave = leave_dates.get(assignment.employee_id, set())

            # Calculate which work days fall in this month
            for day_num in range(1, days_in_month + 1):
                current_date = date(year, month, day_num)

                # Check if date falls within assignment effective dates
                if current_date < assignment.effective_from:
                    continue
                if assignment.effective_to and current_date > assignment.effective_to:
                    continue

                # Determine shift type based on rotation
                shift_type_id = self._determine_shift_type(
                    pattern, assignment, current_date, month_start
                )
                if shift_type_id is None:
                    continue

                # Pattern lines already encode day-level selection, so legacy
                # work-day filtering only applies when no lines are configured.
                if not pattern.pattern_lines:
                    weekday = current_date.weekday()
                    weekday_name = list(DAY_TO_WEEKDAY.keys())[weekday]
                    work_days = self._get_work_days_for_shift(pattern, shift_type_id)
                    if weekday_name not in work_days:
                        continue

                # Check if employee is on leave
                if current_date in employee_leave:
                    skipped_on_leave += 1
                    continue

                # Create schedule entry
                schedule = ShiftSchedule(
                    organization_id=org_id,
                    employee_id=assignment.employee_id,
                    department_id=department_id,
                    shift_date=current_date,
                    shift_type_id=shift_type_id,
                    schedule_month=year_month,
                    status=ScheduleStatus.DRAFT,
                    created_by_id=created_by_id,
                )
                self.db.add(schedule)
                schedules_created += 1
                employee_ids_scheduled.add(assignment.employee_id)

        self.db.flush()

        logger.info(
            "Generated %d schedules for %d employees in department %s for %s",
            schedules_created,
            len(employee_ids_scheduled),
            department_id,
            year_month,
        )

        return {
            "year_month": year_month,
            "department_id": str(department_id),
            "schedules_created": schedules_created,
            "employees_scheduled": len(employee_ids_scheduled),
            "skipped_on_leave": skipped_on_leave,
        }

    def publish_schedule(
        self,
        org_id: UUID,
        department_id: UUID,
        year_month: str,
        published_by_id: UUID | None = None,
    ) -> int:
        """
        Publish all DRAFT schedules for a month.

        Args:
            org_id: Organization ID
            department_id: Department ID
            year_month: Month in YYYY-MM format
            published_by_id: Employee who is publishing

        Returns:
            Number of schedules published
        """
        # Get all draft schedules for the month
        schedules = list(
            self.db.scalars(
                select(ShiftSchedule).where(
                    ShiftSchedule.organization_id == org_id,
                    ShiftSchedule.department_id == department_id,
                    ShiftSchedule.schedule_month == year_month,
                    ShiftSchedule.status == ScheduleStatus.DRAFT,
                )
            ).all()
        )

        if not schedules:
            raise ScheduleGeneratorError(
                f"No draft schedules found for {year_month} in this department"
            )

        now = datetime.now(UTC)
        for schedule in schedules:
            schedule.status = ScheduleStatus.PUBLISHED
            schedule.published_at = now
            schedule.published_by_id = published_by_id

        self.db.flush()

        logger.info(
            "Published %d schedules for department %s for %s",
            len(schedules),
            department_id,
            year_month,
        )

        # Send notifications to affected employees
        self._notify_schedule_published(org_id, schedules, year_month)

        return len(schedules)

    def _notify_schedule_published(
        self,
        org_id: UUID,
        schedules: list[ShiftSchedule],
        year_month: str,
    ) -> None:
        """Notify employees that their schedule has been published."""
        from app.models.people.hr.employee import Employee

        # Get unique employee IDs
        employee_ids = list({s.employee_id for s in schedules})

        # Look up person_id for each employee (for notifications)
        employees = list(
            self.db.scalars(
                select(Employee).where(Employee.employee_id.in_(employee_ids))
            ).all()
        )

        for emp in employees:
            if not emp.person_id:
                continue

            try:
                _notification_service.create(
                    self.db,
                    organization_id=org_id,
                    recipient_id=emp.person_id,
                    entity_type=EntityType.SYSTEM,
                    entity_id=emp.employee_id,
                    notification_type=NotificationType.INFO,
                    title="Schedule Published",
                    message=f"Your shift schedule for {year_month} has been published. Please review your assigned shifts.",
                    channel=NotificationChannel.BOTH,
                    action_url=f"/people/self/scheduling/schedules?year_month={year_month}",
                )
            except Exception as e:
                logger.warning(
                    "Failed to send schedule notification to %s: %s", emp.employee_id, e
                )

    def delete_month_schedules(
        self,
        org_id: UUID,
        department_id: UUID,
        year_month: str,
    ) -> int:
        """
        Delete all DRAFT schedules for a month (for regeneration).

        Only DRAFT schedules can be deleted.
        """
        schedules = list(
            self.db.scalars(
                select(ShiftSchedule).where(
                    ShiftSchedule.organization_id == org_id,
                    ShiftSchedule.department_id == department_id,
                    ShiftSchedule.schedule_month == year_month,
                    ShiftSchedule.status == ScheduleStatus.DRAFT,
                )
            ).all()
        )

        count = len(schedules)
        for schedule in schedules:
            self.db.delete(schedule)

        self.db.flush()
        logger.info(
            "Deleted %d draft schedules for department %s for %s",
            count,
            department_id,
            year_month,
        )

        return count

    def _get_active_assignments(
        self,
        org_id: UUID,
        department_id: UUID,
        year: int,
        month: int,
    ) -> list[ShiftPatternAssignment]:
        """Get all active pattern assignments for a department that overlap the month."""
        _, days_in_month = monthrange(year, month)
        month_start = date(year, month, 1)
        month_end = date(year, month, days_in_month)

        return list(
            self.db.scalars(
                select(ShiftPatternAssignment)
                .options(joinedload(ShiftPatternAssignment.shift_pattern))
                .join(
                    ShiftPattern,
                    ShiftPattern.shift_pattern_id
                    == ShiftPatternAssignment.shift_pattern_id,
                )
                .where(
                    ShiftPatternAssignment.organization_id == org_id,
                    ShiftPatternAssignment.department_id == department_id,
                    ShiftPatternAssignment.is_active == True,  # noqa: E712
                    ShiftPattern.is_active == True,  # noqa: E712
                    ShiftPatternAssignment.effective_from <= month_end,
                    or_(
                        ShiftPatternAssignment.effective_to.is_(None),
                        ShiftPatternAssignment.effective_to >= month_start,
                    ),
                )
            )
            .unique()
            .all()
        )

    def _get_leave_dates_for_month(
        self,
        org_id: UUID,
        employee_ids: list[UUID],
        year: int,
        month: int,
    ) -> dict[UUID, set[date]]:
        """
        Get approved leave dates for employees in a month.

        Returns a dict mapping employee_id to set of dates they are on leave.
        """
        if not employee_ids:
            return {}

        _, days_in_month = monthrange(year, month)
        month_start = date(year, month, 1)
        month_end = date(year, month, days_in_month)

        # Get approved leave applications overlapping the month
        leave_apps = list(
            self.db.scalars(
                select(LeaveApplication).where(
                    LeaveApplication.organization_id == org_id,
                    LeaveApplication.employee_id.in_(employee_ids),
                    LeaveApplication.status == LeaveApplicationStatus.APPROVED,
                    LeaveApplication.from_date <= month_end,
                    LeaveApplication.to_date >= month_start,
                )
            ).all()
        )

        # Build mapping of employee to leave dates
        leave_dates: dict[UUID, set[date]] = {}

        for app in leave_apps:
            if app.employee_id not in leave_dates:
                leave_dates[app.employee_id] = set()

            # Add each date in the leave range that falls in our month
            current = max(app.from_date, month_start)
            end_date = min(app.to_date, month_end)

            while current <= end_date:
                leave_dates[app.employee_id].add(current)
                current += timedelta(days=1)

        return leave_dates

    def _determine_shift_type(
        self,
        pattern: ShiftPattern,
        assignment: ShiftPatternAssignment,
        current_date: date,
        month_start: date,
    ) -> UUID | None:
        """
        Determine the shift type for a given date based on pattern rotation.

        For DAY_ONLY/NIGHT_ONLY: Always returns the respective shift type.
        For ROTATING: Calculates which week of the cycle we're in and returns
        the appropriate shift type based on week offset.
        """
        if pattern.rotation_type == RotationType.DAY_ONLY:
            return pattern.day_shift_type_id

        if pattern.rotation_type == RotationType.NIGHT_ONLY:
            if pattern.night_shift_type_id:
                return pattern.night_shift_type_id
            return pattern.day_shift_type_id

        # ROTATING pattern with explicit lines
        line_found, line_shift = self._resolve_pattern_line_shift(
            pattern, assignment, current_date
        )
        if line_found:
            return line_shift

        # Legacy ROTATING pattern behavior
        # Calculate which week of the cycle we're in
        # Use a reference point (the pattern's effective_from or assignment's effective_from)
        reference_date = assignment.effective_from

        # Days since reference
        days_since_start = (current_date - reference_date).days
        if days_since_start < 0:
            days_since_start = 0

        # Which week are we in (0-indexed)
        week_number = days_since_start // 7

        # Which week of the cycle (accounting for cycle_weeks)
        cycle_week = week_number % pattern.cycle_weeks

        # Apply offset
        adjusted_week = (
            cycle_week + assignment.rotation_week_offset
        ) % pattern.cycle_weeks

        # Even cycle weeks = day shift, odd = night shift (for 2-week cycles)
        # For longer cycles, alternate every other week
        if adjusted_week % 2 == 0:
            return pattern.day_shift_type_id
        else:
            return pattern.night_shift_type_id or pattern.day_shift_type_id

    def _resolve_pattern_line_shift(
        self,
        pattern: ShiftPattern,
        assignment: ShiftPatternAssignment,
        current_date: date,
    ) -> tuple[bool, UUID | None]:
        """Resolve shift assignment from explicit pattern lines when provided."""
        if pattern.rotation_type != RotationType.ROTATING or not pattern.pattern_lines:
            return (False, None)

        days_since_start = max((current_date - assignment.effective_from).days, 0)
        week_number = days_since_start // 7
        adjusted_week = (week_number + assignment.rotation_week_offset) % 2
        week_index = adjusted_week + 1
        weekday_name = list(DAY_TO_WEEKDAY.keys())[current_date.weekday()]

        for line in pattern.pattern_lines:
            if line.get("week_index") == week_index and line.get("day") == weekday_name:
                slot = line.get("shift_slot")
                if slot == "OFF":
                    return (True, None)
                if slot == "NIGHT":
                    return (
                        True,
                        pattern.night_shift_type_id or pattern.day_shift_type_id,
                    )
                return (True, pattern.day_shift_type_id)

        return (False, None)

    def _get_work_days_for_shift(
        self,
        pattern: ShiftPattern,
        shift_type_id: UUID,
    ) -> list[str]:
        """
        Resolve applicable work days for a given pattern and selected shift type.

        For rotating patterns, use the shift-specific day lists when present and
        fallback to legacy `work_days` for backward compatibility.
        """
        if pattern.rotation_type != RotationType.ROTATING:
            return pattern.work_days

        if shift_type_id == pattern.day_shift_type_id and pattern.day_work_days:
            return pattern.day_work_days

        if (
            pattern.night_shift_type_id
            and shift_type_id == pattern.night_shift_type_id
            and pattern.night_work_days
        ):
            return pattern.night_work_days

        return pattern.work_days
