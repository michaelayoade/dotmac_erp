"""
Resource Allocation Service - PM Module.

Business logic for resource allocation and utilization tracking.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Dict, List, Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.pm import ResourceAllocation, TimeEntry
from app.services.common import (
    ConflictError,
    NotFoundError,
    PaginatedResult,
    PaginationParams,
    ValidationError,
    paginate,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.auth import Principal

__all__ = ["ResourceService"]


class ResourceService:
    """
    Service for resource allocation and utilization.

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

    def get_allocation(self, allocation_id: uuid.UUID) -> Optional[ResourceAllocation]:
        """Fetch a single allocation by ID."""
        stmt = (
            select(ResourceAllocation)
            .where(
                ResourceAllocation.allocation_id == allocation_id,
                ResourceAllocation.organization_id == self.organization_id,
            )
            .options(
                selectinload(ResourceAllocation.project),
                selectinload(ResourceAllocation.employee),
            )
        )
        return self.db.scalars(stmt).first()

    def get_allocation_or_raise(self, allocation_id: uuid.UUID) -> ResourceAllocation:
        """Fetch an allocation or raise NotFoundError."""
        allocation = self.get_allocation(allocation_id)
        if not allocation:
            raise NotFoundError(f"Resource allocation {allocation_id} not found")
        return allocation

    def list_allocations(
        self,
        project_id: Optional[uuid.UUID] = None,
        employee_id: Optional[uuid.UUID] = None,
        is_active: Optional[bool] = None,
        params: Optional[PaginationParams] = None,
    ) -> PaginatedResult[ResourceAllocation]:
        """List resource allocations with filtering and pagination."""
        stmt = (
            select(ResourceAllocation)
            .where(ResourceAllocation.organization_id == self.organization_id)
            .options(
                selectinload(ResourceAllocation.project),
                selectinload(ResourceAllocation.employee),
            )
            .order_by(
                ResourceAllocation.is_active.desc(),
                ResourceAllocation.start_date.desc(),
            )
        )

        if project_id:
            stmt = stmt.where(ResourceAllocation.project_id == project_id)
        if employee_id:
            stmt = stmt.where(ResourceAllocation.employee_id == employee_id)
        if is_active is not None:
            stmt = stmt.where(ResourceAllocation.is_active == is_active)

        return paginate(self.db, stmt, params)

    def get_project_team(self, project_id: uuid.UUID) -> List[ResourceAllocation]:
        """Get all active team members for a project."""
        stmt = (
            select(ResourceAllocation)
            .where(
                ResourceAllocation.project_id == project_id,
                ResourceAllocation.organization_id == self.organization_id,
                ResourceAllocation.is_active == True,  # noqa: E712
            )
            .options(selectinload(ResourceAllocation.employee))
            .order_by(ResourceAllocation.start_date)
        )
        return list(self.db.scalars(stmt).all())

    def get_employee_allocations(
        self,
        employee_id: uuid.UUID,
        include_past: bool = False,
    ) -> List[ResourceAllocation]:
        """Get all allocations for an employee."""
        stmt = (
            select(ResourceAllocation)
            .where(
                ResourceAllocation.employee_id == employee_id,
                ResourceAllocation.organization_id == self.organization_id,
            )
            .options(selectinload(ResourceAllocation.project))
            .order_by(ResourceAllocation.start_date.desc())
        )

        if not include_past:
            today = date.today()
            stmt = stmt.where(
                or_(
                    ResourceAllocation.end_date.is_(None),
                    ResourceAllocation.end_date >= today,
                )
            )

        return list(self.db.scalars(stmt).all())

    def get_current_allocations(
        self, employee_id: uuid.UUID
    ) -> List[ResourceAllocation]:
        """Get currently active allocations for an employee."""
        today = date.today()
        stmt = (
            select(ResourceAllocation)
            .where(
                ResourceAllocation.employee_id == employee_id,
                ResourceAllocation.organization_id == self.organization_id,
                ResourceAllocation.is_active == True,  # noqa: E712
                ResourceAllocation.start_date <= today,
                or_(
                    ResourceAllocation.end_date.is_(None),
                    ResourceAllocation.end_date >= today,
                ),
            )
            .options(selectinload(ResourceAllocation.project))
        )
        return list(self.db.scalars(stmt).all())

    # =========================================================================
    # Write Operations
    # =========================================================================

    def allocate_resource(self, data: Dict) -> ResourceAllocation:
        """Allocate an employee to a project."""
        # Check for overlapping allocation
        existing = self._get_overlapping_allocation(
            project_id=data["project_id"],
            employee_id=data["employee_id"],
            start_date=data["start_date"],
            end_date=data.get("end_date"),
        )
        if existing:
            raise ConflictError(
                f"Employee already allocated to this project from {existing.start_date}"
            )

        # Validate allocation percentage
        allocation_percent = data.get("allocation_percent", Decimal("100"))
        total = self._get_total_allocation(
            employee_id=data["employee_id"],
            as_of_date=data["start_date"],
        )
        if total + allocation_percent > Decimal("100"):
            raise ValidationError(
                f"Total allocation would exceed 100% ({total + allocation_percent}%)"
            )

        allocation = ResourceAllocation(
            organization_id=self.organization_id,
            project_id=data["project_id"],
            employee_id=data["employee_id"],
            role_on_project=data.get("role_on_project"),
            allocation_percent=allocation_percent,
            start_date=data["start_date"],
            end_date=data.get("end_date"),
            cost_rate_per_hour=data.get("cost_rate_per_hour"),
            billing_rate_per_hour=data.get("billing_rate_per_hour"),
        )

        if self.principal and hasattr(self.principal, "person_id"):
            allocation.created_by_id = self.principal.person_id

        self.db.add(allocation)
        self.db.flush()
        return allocation

    def update_allocation(
        self, allocation_id: uuid.UUID, data: Dict
    ) -> ResourceAllocation:
        """Update an existing resource allocation."""
        allocation = self.get_allocation_or_raise(allocation_id)

        updatable_fields = [
            "role_on_project",
            "allocation_percent",
            "end_date",
            "is_active",
            "cost_rate_per_hour",
            "billing_rate_per_hour",
        ]

        for field in updatable_fields:
            if field in data and data[field] is not None:
                setattr(allocation, field, data[field])

        if self.principal and hasattr(self.principal, "person_id"):
            allocation.updated_by_id = self.principal.person_id

        return allocation

    def end_allocation(
        self,
        allocation_id: uuid.UUID,
        end_date_value: Optional[date] = None,
    ) -> ResourceAllocation:
        """End a resource allocation."""
        allocation = self.get_allocation_or_raise(allocation_id)

        if not allocation.is_active:
            raise ConflictError("Allocation is already ended")

        allocation.end_date = end_date_value or date.today()
        allocation.is_active = False

        if self.principal and hasattr(self.principal, "person_id"):
            allocation.updated_by_id = self.principal.person_id

        return allocation

    def delete_allocation(self, allocation_id: uuid.UUID) -> bool:
        """Delete a resource allocation."""
        allocation = self.get_allocation_or_raise(allocation_id)
        self.db.delete(allocation)
        return True

    # =========================================================================
    # Utilization
    # =========================================================================

    def get_utilization(
        self,
        employee_id: uuid.UUID,
        start_date: date,
        end_date: date,
    ) -> Dict:
        """Get utilization summary for an employee over a period."""
        # Get allocations for the period
        allocations = self.get_current_allocations(employee_id)
        total_allocation = sum(a.allocation_percent for a in allocations)

        # Get time entries for the period
        stmt = select(func.sum(TimeEntry.hours)).where(
            TimeEntry.employee_id == employee_id,
            TimeEntry.organization_id == self.organization_id,
            TimeEntry.entry_date >= start_date,
            TimeEntry.entry_date <= end_date,
        )
        total_hours = self.db.scalar(stmt) or Decimal("0")

        # Calculate expected hours (assuming 8 hours/day, 5 days/week)
        business_days = self._count_business_days(start_date, end_date)
        expected_hours = Decimal(business_days) * Decimal("8")

        # Calculate utilization
        utilization_percent = Decimal("0")
        if expected_hours > 0:
            utilization_percent = (total_hours / expected_hours) * Decimal("100")

        return {
            "employee_id": employee_id,
            "period_start": start_date,
            "period_end": end_date,
            "total_allocation_percent": total_allocation,
            "hours_logged": total_hours,
            "expected_hours": expected_hours,
            "utilization_percent": utilization_percent,
            "project_allocations": [
                {
                    "project_id": a.project_id,
                    "project_name": a.project.project_name if a.project else None,
                    "allocation_percent": a.allocation_percent,
                    "role_on_project": a.role_on_project,
                }
                for a in allocations
            ],
        }

    def get_project_utilization(self, project_id: uuid.UUID) -> Dict:
        """Get utilization summary for a project."""
        team = self.get_project_team(project_id)

        total_allocation = sum(m.allocation_percent for m in team)
        average_allocation = total_allocation / len(team) if team else Decimal("0")

        # Get total hours logged
        stmt = select(func.sum(TimeEntry.hours)).where(
            TimeEntry.project_id == project_id,
            TimeEntry.organization_id == self.organization_id,
        )
        total_hours = self.db.scalar(stmt) or Decimal("0")

        # Get billable hours
        stmt = select(func.sum(TimeEntry.hours)).where(
            TimeEntry.project_id == project_id,
            TimeEntry.organization_id == self.organization_id,
            TimeEntry.is_billable == True,  # noqa: E712
        )
        billable_hours = self.db.scalar(stmt) or Decimal("0")

        billable_percent = Decimal("0")
        if total_hours > 0:
            billable_percent = (billable_hours / total_hours) * Decimal("100")

        return {
            "project_id": project_id,
            "total_team_members": len(team),
            "total_allocated_percent": total_allocation,
            "average_allocation": average_allocation,
            "total_hours_logged": total_hours,
            "billable_hours": billable_hours,
            "billable_percent": billable_percent,
        }

    # =========================================================================
    # Private Helpers
    # =========================================================================

    def _get_overlapping_allocation(
        self,
        project_id: uuid.UUID,
        employee_id: uuid.UUID,
        start_date: date,
        end_date: Optional[date] = None,
    ) -> Optional[ResourceAllocation]:
        """Check for overlapping allocation."""
        stmt = select(ResourceAllocation).where(
            ResourceAllocation.project_id == project_id,
            ResourceAllocation.employee_id == employee_id,
            ResourceAllocation.organization_id == self.organization_id,
            ResourceAllocation.is_active == True,  # noqa: E712
        )

        if end_date:
            # Check if new period overlaps with existing
            stmt = stmt.where(
                or_(
                    and_(
                        ResourceAllocation.start_date <= start_date,
                        or_(
                            ResourceAllocation.end_date.is_(None),
                            ResourceAllocation.end_date >= start_date,
                        ),
                    ),
                    and_(
                        ResourceAllocation.start_date <= end_date,
                        or_(
                            ResourceAllocation.end_date.is_(None),
                            ResourceAllocation.end_date >= start_date,
                        ),
                    ),
                )
            )
        else:
            # Open-ended allocation
            stmt = stmt.where(
                or_(
                    ResourceAllocation.end_date.is_(None),
                    ResourceAllocation.end_date >= start_date,
                )
            )

        return self.db.scalars(stmt).first()

    def _get_total_allocation(
        self, employee_id: uuid.UUID, as_of_date: date
    ) -> Decimal:
        """Get total allocation percentage for an employee as of a date."""
        stmt = select(func.sum(ResourceAllocation.allocation_percent)).where(
            ResourceAllocation.employee_id == employee_id,
            ResourceAllocation.organization_id == self.organization_id,
            ResourceAllocation.is_active == True,  # noqa: E712
            ResourceAllocation.start_date <= as_of_date,
            or_(
                ResourceAllocation.end_date.is_(None),
                ResourceAllocation.end_date >= as_of_date,
            ),
        )
        return self.db.scalar(stmt) or Decimal("0")

    def _count_business_days(self, start_date: date, end_date: date) -> int:
        """Count business days (Mon-Fri) between two dates."""
        from datetime import timedelta

        count = 0
        current = start_date
        while current <= end_date:
            if current.weekday() < 5:  # Monday = 0, Friday = 4
                count += 1
            current += timedelta(days=1)
        return count
