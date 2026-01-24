"""
Time Entry Service - PM Module.

Business logic for time tracking and timesheets.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Dict, List, Optional

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, selectinload

from app.models.pm import BillingStatus, Task, TimeEntry
from app.services.common import (
    NotFoundError,
    PaginatedResult,
    PaginationParams,
    ValidationError,
    paginate,
)

if TYPE_CHECKING:
    from app.auth import Principal

__all__ = ["TimeEntryService"]


class TimeEntryService:
    """
    Service for time entry and timesheet business logic.

    All mutation methods do NOT commit. Caller is responsible for db.commit().
    """

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        principal: Optional["Principal"] = None,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.principal = principal

    # =========================================================================
    # Read Operations
    # =========================================================================

    def get_entry(self, entry_id: uuid.UUID) -> Optional[TimeEntry]:
        """Fetch a single time entry by ID."""
        stmt = (
            select(TimeEntry)
            .where(
                TimeEntry.entry_id == entry_id,
                TimeEntry.organization_id == self.organization_id,
            )
            .options(
                selectinload(TimeEntry.project),
                selectinload(TimeEntry.task),
                selectinload(TimeEntry.employee),
            )
        )
        return self.db.scalars(stmt).first()

    def get_entry_or_raise(self, entry_id: uuid.UUID) -> TimeEntry:
        """Fetch a time entry or raise NotFoundError."""
        entry = self.get_entry(entry_id)
        if not entry:
            raise NotFoundError(f"Time entry {entry_id} not found")
        return entry

    def list_entries(
        self,
        project_id: Optional[uuid.UUID] = None,
        task_id: Optional[uuid.UUID] = None,
        employee_id: Optional[uuid.UUID] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        is_billable: Optional[bool] = None,
        billing_status: Optional[BillingStatus] = None,
        params: Optional[PaginationParams] = None,
    ) -> PaginatedResult[TimeEntry]:
        """List time entries with filtering and pagination."""
        stmt = (
            select(TimeEntry)
            .where(TimeEntry.organization_id == self.organization_id)
            .options(
                selectinload(TimeEntry.project),
                selectinload(TimeEntry.task),
                selectinload(TimeEntry.employee),
            )
            .order_by(TimeEntry.entry_date.desc(), TimeEntry.created_at.desc())
        )

        if project_id:
            stmt = stmt.where(TimeEntry.project_id == project_id)
        if task_id:
            stmt = stmt.where(TimeEntry.task_id == task_id)
        if employee_id:
            stmt = stmt.where(TimeEntry.employee_id == employee_id)
        if start_date:
            stmt = stmt.where(TimeEntry.entry_date >= start_date)
        if end_date:
            stmt = stmt.where(TimeEntry.entry_date <= end_date)
        if is_billable is not None:
            stmt = stmt.where(TimeEntry.is_billable == is_billable)
        if billing_status:
            stmt = stmt.where(TimeEntry.billing_status == billing_status)

        return paginate(self.db, stmt, params)

    def get_employee_timesheet(
        self,
        employee_id: uuid.UUID,
        week_start: date,
    ) -> List[TimeEntry]:
        """Get time entries for an employee for a specific week."""
        week_end = week_start + timedelta(days=6)
        stmt = (
            select(TimeEntry)
            .where(
                TimeEntry.employee_id == employee_id,
                TimeEntry.organization_id == self.organization_id,
                TimeEntry.entry_date >= week_start,
                TimeEntry.entry_date <= week_end,
            )
            .options(
                selectinload(TimeEntry.project),
                selectinload(TimeEntry.task),
            )
            .order_by(TimeEntry.entry_date, TimeEntry.created_at)
        )
        return list(self.db.scalars(stmt).all())

    def get_project_time_entries(
        self,
        project_id: uuid.UUID,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[TimeEntry]:
        """Get all time entries for a project."""
        stmt = (
            select(TimeEntry)
            .where(
                TimeEntry.project_id == project_id,
                TimeEntry.organization_id == self.organization_id,
            )
            .options(
                selectinload(TimeEntry.task),
                selectinload(TimeEntry.employee),
            )
            .order_by(TimeEntry.entry_date.desc())
        )

        if start_date:
            stmt = stmt.where(TimeEntry.entry_date >= start_date)
        if end_date:
            stmt = stmt.where(TimeEntry.entry_date <= end_date)

        return list(self.db.scalars(stmt).all())

    # =========================================================================
    # Write Operations
    # =========================================================================

    def log_time(self, data: Dict) -> TimeEntry:
        """Log a new time entry."""
        # Validate hours
        hours = data["hours"]
        if hours <= 0:
            raise ValidationError("Hours must be greater than 0")
        if hours > 24:
            raise ValidationError("Hours cannot exceed 24 for a single entry")

        # Validate task belongs to project if specified
        if data.get("task_id"):
            task = self.db.scalars(
                select(Task).where(
                    Task.task_id == data["task_id"],
                    Task.organization_id == self.organization_id,
                )
            ).first()
            if not task:
                raise NotFoundError(f"Task {data['task_id']} not found")
            if task.project_id != data["project_id"]:
                raise ValidationError("Task does not belong to the specified project")

        entry = TimeEntry(
            organization_id=self.organization_id,
            project_id=data["project_id"],
            task_id=data.get("task_id"),
            employee_id=data["employee_id"],
            entry_date=data["entry_date"],
            hours=hours,
            description=data.get("description"),
            is_billable=data.get("is_billable", True),
        )

        if not entry.is_billable:
            entry.billing_status = BillingStatus.NON_BILLABLE

        if self.principal and hasattr(self.principal, "person_id"):
            entry.created_by_id = self.principal.person_id

        self.db.add(entry)
        self.db.flush()

        # Update task actual hours if linked to a task
        if entry.task_id:
            self._update_task_hours(entry.task_id)

        return entry

    def update_entry(self, entry_id: uuid.UUID, data: Dict) -> TimeEntry:
        """Update an existing time entry."""
        entry = self.get_entry_or_raise(entry_id)

        # Cannot update billed entries
        if entry.billing_status == BillingStatus.BILLED:
            raise ValidationError("Cannot update a billed time entry")

        old_task_id = entry.task_id
        old_hours = entry.hours

        updatable_fields = [
            "task_id",
            "entry_date",
            "hours",
            "description",
            "is_billable",
        ]

        for field in updatable_fields:
            if field in data and data[field] is not None:
                setattr(entry, field, data[field])

        # Update billing status based on is_billable
        if "is_billable" in data:
            if not data["is_billable"]:
                entry.billing_status = BillingStatus.NON_BILLABLE
            elif entry.billing_status == BillingStatus.NON_BILLABLE:
                entry.billing_status = BillingStatus.NOT_BILLED

        if self.principal and hasattr(self.principal, "person_id"):
            entry.updated_by_id = self.principal.person_id

        # Update task hours if needed
        if old_task_id and (old_task_id != entry.task_id or old_hours != entry.hours):
            self._update_task_hours(old_task_id)
        if entry.task_id:
            self._update_task_hours(entry.task_id)

        return entry

    def delete_entry(self, entry_id: uuid.UUID) -> bool:
        """Delete a time entry."""
        entry = self.get_entry_or_raise(entry_id)

        # Cannot delete billed entries
        if entry.billing_status == BillingStatus.BILLED:
            raise ValidationError("Cannot delete a billed time entry")

        task_id = entry.task_id
        self.db.delete(entry)

        # Update task hours
        if task_id:
            self._update_task_hours(task_id)

        return True

    def mark_billed(self, entry_ids: List[uuid.UUID]) -> int:
        """Mark multiple time entries as billed."""
        count = 0
        for entry_id in entry_ids:
            entry = self.get_entry(entry_id)
            if entry and entry.is_billable and entry.billing_status == BillingStatus.NOT_BILLED:
                entry.billing_status = BillingStatus.BILLED
                count += 1
        return count

    # =========================================================================
    # Summaries
    # =========================================================================

    def get_project_time_summary(self, project_id: uuid.UUID) -> Dict:
        """Get time summary for a project."""
        base_where = and_(
            TimeEntry.project_id == project_id,
            TimeEntry.organization_id == self.organization_id,
        )

        # Total hours
        total_hours = self.db.scalar(
            select(func.sum(TimeEntry.hours)).where(base_where)
        ) or Decimal("0")

        # Billable hours
        billable_hours = self.db.scalar(
            select(func.sum(TimeEntry.hours)).where(
                base_where,
                TimeEntry.is_billable == True,  # noqa: E712
            )
        ) or Decimal("0")

        # Billed hours
        billed_hours = self.db.scalar(
            select(func.sum(TimeEntry.hours)).where(
                base_where,
                TimeEntry.billing_status == BillingStatus.BILLED,
            )
        ) or Decimal("0")

        # Hours by employee
        employee_hours = self.db.execute(
            select(
                TimeEntry.employee_id,
                func.sum(TimeEntry.hours).label("hours"),
            )
            .where(base_where)
            .group_by(TimeEntry.employee_id)
        ).all()

        # Hours by task
        task_hours = self.db.execute(
            select(
                TimeEntry.task_id,
                func.sum(TimeEntry.hours).label("hours"),
            )
            .where(base_where, TimeEntry.task_id.isnot(None))
            .group_by(TimeEntry.task_id)
        ).all()

        return {
            "project_id": project_id,
            "total_hours": total_hours,
            "billable_hours": billable_hours,
            "non_billable_hours": total_hours - billable_hours,
            "billed_hours": billed_hours,
            "unbilled_hours": billable_hours - billed_hours,
            "hours_by_employee": {
                str(emp_id): hours for emp_id, hours in employee_hours
            },
            "hours_by_task": {
                str(task_id): hours for task_id, hours in task_hours
            },
        }

    def get_employee_time_summary(
        self,
        employee_id: uuid.UUID,
        start_date: date,
        end_date: date,
    ) -> Dict:
        """Get time summary for an employee over a period."""
        base_where = and_(
            TimeEntry.employee_id == employee_id,
            TimeEntry.organization_id == self.organization_id,
            TimeEntry.entry_date >= start_date,
            TimeEntry.entry_date <= end_date,
        )

        # Total hours
        total_hours = self.db.scalar(
            select(func.sum(TimeEntry.hours)).where(base_where)
        ) or Decimal("0")

        # Billable hours
        billable_hours = self.db.scalar(
            select(func.sum(TimeEntry.hours)).where(
                base_where,
                TimeEntry.is_billable == True,  # noqa: E712
            )
        ) or Decimal("0")

        # Hours by project
        project_hours = self.db.execute(
            select(
                TimeEntry.project_id,
                func.sum(TimeEntry.hours).label("hours"),
            )
            .where(base_where)
            .group_by(TimeEntry.project_id)
        ).all()

        return {
            "employee_id": employee_id,
            "period_start": start_date,
            "period_end": end_date,
            "total_hours": total_hours,
            "billable_hours": billable_hours,
            "hours_by_project": {
                str(proj_id): hours for proj_id, hours in project_hours
            },
        }

    # =========================================================================
    # Private Helpers
    # =========================================================================

    def _update_task_hours(self, task_id: uuid.UUID) -> None:
        """Update a task's actual_hours based on time entries."""
        total_hours = self.db.scalar(
            select(func.sum(TimeEntry.hours)).where(
                TimeEntry.task_id == task_id,
                TimeEntry.organization_id == self.organization_id,
            )
        ) or Decimal("0")

        task = self.db.scalars(
            select(Task).where(Task.task_id == task_id)
        ).first()

        if task:
            task.actual_hours = total_hours
