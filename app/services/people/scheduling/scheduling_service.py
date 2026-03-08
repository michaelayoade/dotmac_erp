"""
Shift Scheduling Service.

Handles shift patterns, pattern assignments, and schedule queries.
"""

from __future__ import annotations

import logging
from datetime import date
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.models.people.hr.employee import Employee
from app.models.people.scheduling import (
    RotationType,
    ScheduleStatus,
    ShiftPattern,
    ShiftPatternAssignment,
    ShiftSchedule,
)
from app.services.common import PaginatedResult, PaginationParams

logger = logging.getLogger(__name__)


class SchedulingServiceError(Exception):
    """Base error for scheduling service."""

    pass


class ShiftPatternNotFoundError(SchedulingServiceError):
    """Shift pattern not found."""

    def __init__(self, pattern_id: UUID):
        self.pattern_id = pattern_id
        super().__init__(f"Shift pattern {pattern_id} not found")


class PatternAssignmentNotFoundError(SchedulingServiceError):
    """Pattern assignment not found."""

    def __init__(self, assignment_id: UUID):
        self.assignment_id = assignment_id
        super().__init__(f"Pattern assignment {assignment_id} not found")


class ShiftScheduleNotFoundError(SchedulingServiceError):
    """Shift schedule not found."""

    def __init__(self, schedule_id: UUID):
        self.schedule_id = schedule_id
        super().__init__(f"Shift schedule {schedule_id} not found")


class SchedulingService:
    """
    Service for shift scheduling operations.

    Handles:
    - Shift pattern CRUD
    - Pattern assignment CRUD
    - Schedule queries
    """

    VALID_WORK_DAYS = {"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"}
    VALID_SHIFT_SLOTS = {"DAY", "NIGHT", "OFF"}

    def __init__(self, db: Session) -> None:
        self.db = db

    # =========================================================================
    # Shift Patterns
    # =========================================================================

    def list_patterns(
        self,
        org_id: UUID,
        *,
        is_active: bool | None = None,
        rotation_type: RotationType | None = None,
        search: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[ShiftPattern]:
        """List shift patterns for an organization."""
        query = (
            select(ShiftPattern)
            .where(ShiftPattern.organization_id == org_id)
            .options(
                joinedload(ShiftPattern.day_shift_type),
                joinedload(ShiftPattern.night_shift_type),
            )
        )

        if is_active is not None:
            query = query.where(ShiftPattern.is_active == is_active)

        if rotation_type is not None:
            query = query.where(ShiftPattern.rotation_type == rotation_type)

        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    ShiftPattern.pattern_code.ilike(search_term),
                    ShiftPattern.pattern_name.ilike(search_term),
                )
            )

        query = query.order_by(ShiftPattern.pattern_name)

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        # Apply pagination
        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).unique().all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_pattern(self, org_id: UUID, pattern_id: UUID) -> ShiftPattern:
        """Get a shift pattern by ID."""
        pattern = self.db.scalar(
            select(ShiftPattern)
            .options(
                joinedload(ShiftPattern.day_shift_type),
                joinedload(ShiftPattern.night_shift_type),
            )
            .where(
                ShiftPattern.shift_pattern_id == pattern_id,
                ShiftPattern.organization_id == org_id,
            )
        )
        if not pattern:
            raise ShiftPatternNotFoundError(pattern_id)
        return pattern

    def create_pattern(
        self,
        org_id: UUID,
        *,
        pattern_code: str,
        pattern_name: str,
        rotation_type: RotationType,
        day_shift_type_id: UUID,
        night_shift_type_id: UUID | None = None,
        cycle_weeks: int = 1,
        work_days: list[str] | None = None,
        day_work_days: list[str] | None = None,
        night_work_days: list[str] | None = None,
        pattern_lines: list[dict] | None = None,
        description: str | None = None,
        is_active: bool = True,
    ) -> ShiftPattern:
        """Create a new shift pattern."""
        # Validate: rotating patterns need night shift
        if rotation_type == RotationType.ROTATING and not night_shift_type_id:
            raise SchedulingServiceError("Rotating patterns require a night shift type")

        resolved_work_days = work_days or ["MON", "TUE", "WED", "THU", "FRI"]
        resolved_day_work_days = day_work_days
        resolved_night_work_days = night_work_days
        if rotation_type == RotationType.ROTATING:
            cycle_weeks = 2
            resolved_day_work_days = (
                day_work_days or work_days or ["MON", "TUE", "WED", "THU", "FRI"]
            )
            resolved_night_work_days = (
                night_work_days or work_days or ["MON", "TUE", "WED", "THU", "FRI"]
            )
        else:
            pattern_lines = None

        self._validate_day_codes("work_days", resolved_work_days)
        if resolved_day_work_days is not None:
            self._validate_day_codes("day_work_days", resolved_day_work_days)
        if resolved_night_work_days is not None:
            self._validate_day_codes("night_work_days", resolved_night_work_days)
        self._validate_pattern_lines(pattern_lines)

        pattern = ShiftPattern(
            organization_id=org_id,
            pattern_code=pattern_code,
            pattern_name=pattern_name,
            description=description,
            rotation_type=rotation_type,
            cycle_weeks=cycle_weeks,
            work_days=resolved_work_days,
            day_work_days=resolved_day_work_days,
            night_work_days=resolved_night_work_days,
            pattern_lines=pattern_lines,
            day_shift_type_id=day_shift_type_id,
            night_shift_type_id=night_shift_type_id,
            is_active=is_active,
        )

        self.db.add(pattern)
        try:
            self.db.flush()
        except IntegrityError as e:
            self.db.rollback()
            if (
                "uq_shift_pattern_org_code" in str(e).lower()
                or "pattern_code" in str(e).lower()
            ):
                raise SchedulingServiceError(
                    f"Pattern code '{pattern_code}' already exists in this organization"
                ) from e
            raise
        logger.info("Created shift pattern: %s", pattern.pattern_code)
        return pattern

    # Fields that can be explicitly set to None (cleared)
    PATTERN_CLEARABLE_FIELDS = {
        "description",
        "night_shift_type_id",
        "day_work_days",
        "night_work_days",
        "pattern_lines",
    }

    def update_pattern(
        self,
        org_id: UUID,
        pattern_id: UUID,
        **kwargs,
    ) -> ShiftPattern:
        """Update a shift pattern."""
        pattern = self.get_pattern(org_id, pattern_id)

        for key, value in kwargs.items():
            if not hasattr(pattern, key):
                continue
            # Allow None for clearable fields, require value for others
            if value is None and key not in self.PATTERN_CLEARABLE_FIELDS:
                continue
            setattr(pattern, key, value)

        # Validate rotating patterns
        if (
            pattern.rotation_type == RotationType.ROTATING
            and not pattern.night_shift_type_id
        ):
            raise SchedulingServiceError("Rotating patterns require a night shift type")
        if pattern.rotation_type != RotationType.ROTATING:
            pattern.pattern_lines = None

        # Maintain backward compatibility for existing rotating patterns:
        # default missing rotating day lists to generic work_days.
        if pattern.rotation_type == RotationType.ROTATING:
            pattern.cycle_weeks = 2
            if not pattern.day_work_days:
                pattern.day_work_days = pattern.work_days or [
                    "MON",
                    "TUE",
                    "WED",
                    "THU",
                    "FRI",
                ]
            if not pattern.night_work_days:
                pattern.night_work_days = pattern.work_days or [
                    "MON",
                    "TUE",
                    "WED",
                    "THU",
                    "FRI",
                ]

        self._validate_day_codes(
            "work_days", pattern.work_days or ["MON", "TUE", "WED", "THU", "FRI"]
        )
        if pattern.day_work_days is not None:
            self._validate_day_codes("day_work_days", pattern.day_work_days)
        if pattern.night_work_days is not None:
            self._validate_day_codes("night_work_days", pattern.night_work_days)
        self._validate_pattern_lines(pattern.pattern_lines)

        try:
            self.db.flush()
        except IntegrityError as e:
            self.db.rollback()
            if (
                "uq_shift_pattern_org_code" in str(e).lower()
                or "pattern_code" in str(e).lower()
            ):
                raise SchedulingServiceError(
                    f"Pattern code '{pattern.pattern_code}' already exists in this organization"
                ) from e
            raise
        logger.info("Updated shift pattern: %s", pattern.pattern_code)
        return pattern

    def _validate_day_codes(self, field_name: str, day_codes: list[str]) -> None:
        """Validate weekday codes used in shift patterns."""
        if not day_codes:
            raise SchedulingServiceError(f"{field_name} cannot be empty")
        invalid_codes = [day for day in day_codes if day not in self.VALID_WORK_DAYS]
        if invalid_codes:
            raise SchedulingServiceError(
                f"{field_name} contains invalid day codes: {invalid_codes}"
            )

    def _validate_pattern_lines(self, pattern_lines: list[dict] | None) -> None:
        """Validate optional rotating pattern lines payload."""
        if pattern_lines is None:
            return
        for idx, line in enumerate(pattern_lines):
            if not isinstance(line, dict):
                raise SchedulingServiceError(f"pattern_lines[{idx}] must be an object")
            week_index = line.get("week_index")
            day = line.get("day")
            shift_slot = line.get("shift_slot")
            if week_index not in (1, 2):
                raise SchedulingServiceError(
                    f"pattern_lines[{idx}].week_index must be 1 or 2"
                )
            if day not in self.VALID_WORK_DAYS:
                raise SchedulingServiceError(
                    f"pattern_lines[{idx}].day must be one of {sorted(self.VALID_WORK_DAYS)}"
                )
            if shift_slot not in self.VALID_SHIFT_SLOTS:
                raise SchedulingServiceError(
                    f"pattern_lines[{idx}].shift_slot must be one of {sorted(self.VALID_SHIFT_SLOTS)}"
                )

    def delete_pattern(self, org_id: UUID, pattern_id: UUID) -> None:
        """Delete a shift pattern (soft delete by deactivating)."""
        pattern = self.get_pattern(org_id, pattern_id)
        pattern.is_active = False
        self.db.flush()
        logger.info("Deactivated shift pattern: %s", pattern.pattern_code)

    # =========================================================================
    # Pattern Assignments
    # =========================================================================

    def list_assignments(
        self,
        org_id: UUID,
        *,
        department_id: UUID | None = None,
        employee_id: UUID | None = None,
        shift_pattern_id: UUID | None = None,
        is_active: bool | None = None,
        effective_date: date | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[ShiftPatternAssignment]:
        """List pattern assignments."""
        query = (
            select(ShiftPatternAssignment)
            .where(ShiftPatternAssignment.organization_id == org_id)
            .options(
                joinedload(ShiftPatternAssignment.employee),
                joinedload(ShiftPatternAssignment.department),
                joinedload(ShiftPatternAssignment.shift_pattern),
            )
        )

        if department_id:
            query = query.where(ShiftPatternAssignment.department_id == department_id)

        if employee_id:
            query = query.where(ShiftPatternAssignment.employee_id == employee_id)

        if shift_pattern_id:
            query = query.where(
                ShiftPatternAssignment.shift_pattern_id == shift_pattern_id
            )

        if is_active is not None:
            query = query.where(ShiftPatternAssignment.is_active == is_active)

        if effective_date:
            query = query.where(
                and_(
                    ShiftPatternAssignment.effective_from <= effective_date,
                    or_(
                        ShiftPatternAssignment.effective_to.is_(None),
                        ShiftPatternAssignment.effective_to >= effective_date,
                    ),
                )
            )

        query = query.order_by(ShiftPatternAssignment.effective_from.desc())

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).unique().all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_assignment(
        self, org_id: UUID, assignment_id: UUID
    ) -> ShiftPatternAssignment:
        """Get a pattern assignment by ID."""
        assignment = self.db.scalar(
            select(ShiftPatternAssignment)
            .options(
                joinedload(ShiftPatternAssignment.employee),
                joinedload(ShiftPatternAssignment.department),
                joinedload(ShiftPatternAssignment.shift_pattern),
            )
            .where(
                ShiftPatternAssignment.pattern_assignment_id == assignment_id,
                ShiftPatternAssignment.organization_id == org_id,
            )
        )
        if not assignment:
            raise PatternAssignmentNotFoundError(assignment_id)
        return assignment

    def create_assignment(
        self,
        org_id: UUID,
        *,
        employee_id: UUID,
        department_id: UUID,
        shift_pattern_id: UUID,
        effective_from: date,
        effective_to: date | None = None,
        rotation_week_offset: int = 0,
        is_active: bool = True,
    ) -> ShiftPatternAssignment:
        """Create a pattern assignment for an employee."""
        # Verify pattern exists
        pattern = self.get_pattern(org_id, shift_pattern_id)
        if not pattern.is_active:
            raise SchedulingServiceError("Cannot assign an inactive shift pattern")
        if effective_to and effective_to < effective_from:
            raise SchedulingServiceError("effective_to cannot be before effective_from")

        # Check for overlapping active assignments for the employee
        self._check_overlapping_assignment(
            org_id, employee_id, department_id, effective_from, effective_to
        )

        assignment = ShiftPatternAssignment(
            organization_id=org_id,
            employee_id=employee_id,
            department_id=department_id,
            shift_pattern_id=shift_pattern_id,
            effective_from=effective_from,
            effective_to=effective_to,
            rotation_week_offset=rotation_week_offset,
            is_active=is_active,
        )

        self.db.add(assignment)
        self.db.flush()
        logger.info(
            "Created pattern assignment: employee=%s, pattern=%s",
            employee_id,
            shift_pattern_id,
        )
        return assignment

    def bulk_create_assignments(
        self,
        org_id: UUID,
        *,
        employee_ids: list[UUID],
        department_id: UUID,
        shift_pattern_id: UUID,
        effective_from: date,
        effective_to: date | None = None,
        rotation_week_offset: int = 0,
    ) -> dict:
        """Bulk create pattern assignments for multiple employees."""
        success_count = 0
        failed_count = 0
        errors: list[dict] = []

        for employee_id in employee_ids:
            try:
                self.create_assignment(
                    org_id=org_id,
                    employee_id=employee_id,
                    department_id=department_id,
                    shift_pattern_id=shift_pattern_id,
                    effective_from=effective_from,
                    effective_to=effective_to,
                    rotation_week_offset=rotation_week_offset,
                )
                success_count += 1
            except Exception as e:
                failed_count += 1
                errors.append(
                    {
                        "employee_id": str(employee_id),
                        "reason": str(e),
                    }
                )

        self.db.flush()
        return {
            "success_count": success_count,
            "failed_count": failed_count,
            "errors": errors,
        }

    # Fields that can be explicitly set to None (cleared)
    ASSIGNMENT_CLEARABLE_FIELDS = {"effective_to"}

    def update_assignment(
        self,
        org_id: UUID,
        assignment_id: UUID,
        **kwargs,
    ) -> ShiftPatternAssignment:
        """Update a pattern assignment."""
        assignment = self.get_assignment(org_id, assignment_id)
        original_employee_id = assignment.employee_id

        for key, value in kwargs.items():
            if not hasattr(assignment, key):
                continue
            # Allow None for clearable fields, require value for others
            if value is None and key not in self.ASSIGNMENT_CLEARABLE_FIELDS:
                continue
            setattr(assignment, key, value)

        if assignment.shift_pattern_id:
            pattern = self.get_pattern(org_id, assignment.shift_pattern_id)
            if not pattern.is_active:
                raise SchedulingServiceError("Cannot assign an inactive shift pattern")

        if (
            assignment.effective_to
            and assignment.effective_to < assignment.effective_from
        ):
            raise SchedulingServiceError("effective_to cannot be before effective_from")

        # Re-run overlap validation when assignment shape changes.
        if (
            assignment.employee_id != original_employee_id
            or "employee_id" in kwargs
            or "department_id" in kwargs
            or "effective_from" in kwargs
            or "effective_to" in kwargs
            or "is_active" in kwargs
        ) and assignment.is_active:
            self._check_overlapping_assignment(
                org_id=org_id,
                employee_id=assignment.employee_id,
                department_id=assignment.department_id,
                effective_from=assignment.effective_from,
                effective_to=assignment.effective_to,
                exclude_assignment_id=assignment.pattern_assignment_id,
            )

        self.db.flush()
        logger.info("Updated pattern assignment: %s", assignment_id)
        return assignment

    def delete_assignment(self, org_id: UUID, assignment_id: UUID) -> None:
        """End a pattern assignment (soft delete by deactivating)."""
        assignment = self.get_assignment(org_id, assignment_id)
        assignment.is_active = False
        if not assignment.effective_to:
            assignment.effective_to = date.today()
        self.db.flush()
        logger.info("Ended pattern assignment: %s", assignment_id)

    # =========================================================================
    # Shift Schedules - Queries
    # =========================================================================

    def list_schedules(
        self,
        org_id: UUID,
        *,
        department_id: UUID | None = None,
        employee_id: UUID | None = None,
        schedule_month: str | None = None,
        status: ScheduleStatus | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[ShiftSchedule]:
        """List shift schedules."""
        self._auto_complete_elapsed_months(org_id)
        query = (
            select(ShiftSchedule)
            .where(ShiftSchedule.organization_id == org_id)
            .options(
                joinedload(ShiftSchedule.employee).joinedload(Employee.person),
                joinedload(ShiftSchedule.department),
                joinedload(ShiftSchedule.shift_type),
            )
        )

        if department_id:
            query = query.where(ShiftSchedule.department_id == department_id)

        if employee_id:
            query = query.where(ShiftSchedule.employee_id == employee_id)

        if schedule_month:
            query = query.where(ShiftSchedule.schedule_month == schedule_month)

        if status:
            query = query.where(ShiftSchedule.status == status)

        if from_date:
            query = query.where(ShiftSchedule.shift_date >= from_date)

        if to_date:
            query = query.where(ShiftSchedule.shift_date <= to_date)

        query = query.order_by(ShiftSchedule.shift_date)

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).unique().all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_schedule(self, org_id: UUID, schedule_id: UUID) -> ShiftSchedule:
        """Get a shift schedule by ID."""
        schedule = self.db.scalar(
            select(ShiftSchedule)
            .options(
                joinedload(ShiftSchedule.employee),
                joinedload(ShiftSchedule.department),
                joinedload(ShiftSchedule.shift_type),
            )
            .where(
                ShiftSchedule.shift_schedule_id == schedule_id,
                ShiftSchedule.organization_id == org_id,
            )
        )
        if not schedule:
            raise ShiftScheduleNotFoundError(schedule_id)
        return schedule

    # Fields that can be explicitly set to None (cleared)
    SCHEDULE_CLEARABLE_FIELDS = {"notes"}

    def update_schedule(
        self,
        org_id: UUID,
        schedule_id: UUID,
        **kwargs,
    ) -> ShiftSchedule:
        """Update a shift schedule entry."""
        schedule = self.get_schedule(org_id, schedule_id)

        # Only allow updates to DRAFT schedules
        if schedule.status != ScheduleStatus.DRAFT:
            raise SchedulingServiceError(
                f"Cannot update schedule in {schedule.status.value} status. "
                "Only DRAFT schedules can be modified."
            )

        for key, value in kwargs.items():
            if not hasattr(schedule, key):
                continue
            # Allow None for clearable fields, require value for others
            if value is None and key not in self.SCHEDULE_CLEARABLE_FIELDS:
                continue
            setattr(schedule, key, value)

        self.db.flush()
        logger.info("Updated schedule: %s", schedule_id)
        return schedule

    def delete_schedule(self, org_id: UUID, schedule_id: UUID) -> None:
        """Delete a shift schedule entry."""
        schedule = self.get_schedule(org_id, schedule_id)

        if schedule.status != ScheduleStatus.DRAFT:
            raise SchedulingServiceError(
                f"Cannot delete schedule in {schedule.status.value} status. "
                "Only DRAFT schedules can be deleted."
            )

        self.db.delete(schedule)
        self.db.flush()
        logger.info("Deleted schedule: %s", schedule_id)

    def get_schedule_status_for_month(
        self,
        org_id: UUID,
        department_id: UUID,
        schedule_month: str,
    ) -> ScheduleStatus | None:
        """Get the overall status for a month's schedule."""
        self._auto_complete_elapsed_months(org_id)
        statuses = set(
            self.db.scalars(
                select(ShiftSchedule.status).where(
                    ShiftSchedule.organization_id == org_id,
                    ShiftSchedule.department_id == department_id,
                    ShiftSchedule.schedule_month == schedule_month,
                )
            ).all()
        )
        if not statuses:
            return None
        if ScheduleStatus.DRAFT in statuses:
            return ScheduleStatus.DRAFT
        if ScheduleStatus.PUBLISHED in statuses:
            return ScheduleStatus.PUBLISHED
        return ScheduleStatus.COMPLETED

    def _auto_complete_elapsed_months(self, org_id: UUID) -> None:
        """
        Mark published schedules as COMPLETED once the month has ended.

        This keeps the schedule lifecycle moving without requiring manual closure.
        """
        today = date.today()
        current_month_key = today.strftime("%Y-%m")
        self.db.execute(
            update(ShiftSchedule)
            .where(
                ShiftSchedule.organization_id == org_id,
                ShiftSchedule.status == ScheduleStatus.PUBLISHED,
                ShiftSchedule.schedule_month < current_month_key,
            )
            .values(status=ScheduleStatus.COMPLETED)
        )

    def get_active_assignment_for_employee(
        self,
        org_id: UUID,
        employee_id: UUID,
        as_of_date: date,
    ) -> ShiftPatternAssignment | None:
        """Get the active pattern assignment for an employee on a given date."""
        return self.db.scalar(
            select(ShiftPatternAssignment)
            .options(joinedload(ShiftPatternAssignment.shift_pattern))
            .where(
                ShiftPatternAssignment.organization_id == org_id,
                ShiftPatternAssignment.employee_id == employee_id,
                ShiftPatternAssignment.is_active == True,  # noqa: E712
                ShiftPatternAssignment.effective_from <= as_of_date,
                or_(
                    ShiftPatternAssignment.effective_to.is_(None),
                    ShiftPatternAssignment.effective_to >= as_of_date,
                ),
            )
        )

    def _check_overlapping_assignment(
        self,
        org_id: UUID,
        employee_id: UUID,
        department_id: UUID,
        effective_from: date,
        effective_to: date | None,
        exclude_assignment_id: UUID | None = None,
    ) -> None:
        """
        Check for overlapping active assignments for the same employee.

        Raises SchedulingServiceError if an overlap is found.
        """
        # Build query to find overlapping assignments.
        # An assignment overlaps if:
        # - Same org, employee
        # - Is active
        # - Date ranges overlap: existing.from <= new.to AND existing.to >= new.from
        #   (accounting for NULL effective_to meaning "ongoing")
        query = select(ShiftPatternAssignment).where(
            ShiftPatternAssignment.organization_id == org_id,
            ShiftPatternAssignment.employee_id == employee_id,
            ShiftPatternAssignment.is_active == True,  # noqa: E712
        )
        if exclude_assignment_id:
            query = query.where(
                ShiftPatternAssignment.pattern_assignment_id != exclude_assignment_id
            )

        # Check for date overlap
        # If new assignment has no end date, it overlaps with anything that starts before or has no end
        if effective_to is None:
            # New assignment is ongoing - overlaps with any existing that doesn't end before our start
            query = query.where(
                or_(
                    ShiftPatternAssignment.effective_to.is_(None),
                    ShiftPatternAssignment.effective_to >= effective_from,
                )
            )
        else:
            # New assignment has an end date - check proper overlap
            query = query.where(
                ShiftPatternAssignment.effective_from <= effective_to,
                or_(
                    ShiftPatternAssignment.effective_to.is_(None),
                    ShiftPatternAssignment.effective_to >= effective_from,
                ),
            )

        existing = self.db.scalar(query)
        if existing:
            raise SchedulingServiceError(
                f"Employee already has an active pattern assignment "
                f"from {existing.effective_from} to {existing.effective_to or 'ongoing'}. "
                f"End or deactivate the existing assignment first."
            )
