"""
Milestone Service - PM Module.

Business logic for milestone management.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.pm import Milestone, MilestoneStatus
from app.services.common import (
    ConflictError,
    NotFoundError,
    PaginatedResult,
    PaginationParams,
    paginate,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.auth import Principal

__all__ = ["MilestoneService"]


class MilestoneService:
    """
    Service for PM Milestone business logic.

    All mutation methods do NOT commit. Caller is responsible for db.commit().
    """

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        principal: Principal | None = None,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.principal = principal

    # =========================================================================
    # Read Operations
    # =========================================================================

    def get_milestone(self, milestone_id: uuid.UUID) -> Milestone | None:
        """Fetch a single milestone by ID."""
        stmt = (
            select(Milestone)
            .where(
                Milestone.milestone_id == milestone_id,
                Milestone.organization_id == self.organization_id,
            )
            .options(
                selectinload(Milestone.project),
                selectinload(Milestone.linked_task),
            )
        )
        return self.db.scalars(stmt).first()

    def get_milestone_or_raise(self, milestone_id: uuid.UUID) -> Milestone:
        """Fetch a milestone or raise NotFoundError."""
        milestone = self.get_milestone(milestone_id)
        if not milestone:
            raise NotFoundError(f"Milestone {milestone_id} not found")
        return milestone

    def list_milestones(
        self,
        project_id: uuid.UUID | None = None,
        status: MilestoneStatus | None = None,
        params: PaginationParams | None = None,
    ) -> PaginatedResult[Milestone]:
        """List milestones with filtering and pagination."""
        stmt = (
            select(Milestone)
            .where(Milestone.organization_id == self.organization_id)
            .options(selectinload(Milestone.project))
            .order_by(Milestone.target_date)
        )

        if project_id:
            stmt = stmt.where(Milestone.project_id == project_id)
        if status:
            stmt = stmt.where(Milestone.status == status)

        return paginate(self.db, stmt, params)

    def get_upcoming_milestones(
        self,
        days: int = 30,
        project_id: uuid.UUID | None = None,
    ) -> list[Milestone]:
        """Get milestones due within the specified number of days."""
        today = date.today()
        end_date = today + timedelta(days=days)

        stmt = (
            select(Milestone)
            .where(
                Milestone.organization_id == self.organization_id,
                Milestone.status == MilestoneStatus.PENDING,
                Milestone.target_date >= today,
                Milestone.target_date <= end_date,
            )
            .options(selectinload(Milestone.project))
            .order_by(Milestone.target_date)
        )

        if project_id:
            stmt = stmt.where(Milestone.project_id == project_id)

        return list(self.db.scalars(stmt).all())

    def get_overdue_milestones(
        self, project_id: uuid.UUID | None = None
    ) -> list[Milestone]:
        """Get milestones that are past target date and not achieved."""
        stmt = (
            select(Milestone)
            .where(
                Milestone.organization_id == self.organization_id,
                Milestone.status == MilestoneStatus.PENDING,
                Milestone.target_date < date.today(),
            )
            .options(selectinload(Milestone.project))
            .order_by(Milestone.target_date)
        )

        if project_id:
            stmt = stmt.where(Milestone.project_id == project_id)

        return list(self.db.scalars(stmt).all())

    def get_project_milestones(self, project_id: uuid.UUID) -> list[Milestone]:
        """Get all milestones for a project."""
        stmt = (
            select(Milestone)
            .where(
                Milestone.project_id == project_id,
                Milestone.organization_id == self.organization_id,
            )
            .order_by(Milestone.target_date)
        )
        return list(self.db.scalars(stmt).all())

    # =========================================================================
    # Write Operations
    # =========================================================================

    def create_milestone(self, data: dict) -> Milestone:
        """Create a new milestone."""
        milestone = Milestone(
            organization_id=self.organization_id,
            project_id=data["project_id"],
            milestone_code=data["milestone_code"],
            milestone_name=data["milestone_name"],
            description=data.get("description"),
            target_date=data["target_date"],
            linked_task_id=data.get("linked_task_id"),
        )

        if self.principal and hasattr(self.principal, "person_id"):
            milestone.created_by_id = self.principal.person_id

        self.db.add(milestone)
        self.db.flush()
        return milestone

    def update_milestone(self, milestone_id: uuid.UUID, data: dict) -> Milestone:
        """Update an existing milestone."""
        milestone = self.get_milestone_or_raise(milestone_id)

        updatable_fields = [
            "milestone_code",
            "milestone_name",
            "description",
            "target_date",
            "linked_task_id",
        ]

        for field in updatable_fields:
            if field in data and data[field] is not None:
                setattr(milestone, field, data[field])

        if self.principal and hasattr(self.principal, "person_id"):
            milestone.updated_by_id = self.principal.person_id

        return milestone

    def delete_milestone(self, milestone_id: uuid.UUID) -> bool:
        """Delete a milestone."""
        milestone = self.get_milestone_or_raise(milestone_id)
        self.db.delete(milestone)
        return True

    # =========================================================================
    # Status Operations
    # =========================================================================

    def achieve_milestone(
        self,
        milestone_id: uuid.UUID,
        actual_date: date | None = None,
    ) -> Milestone:
        """Mark a milestone as achieved."""
        milestone = self.get_milestone_or_raise(milestone_id)

        if milestone.status == MilestoneStatus.ACHIEVED:
            raise ConflictError("Milestone is already achieved")
        if milestone.status == MilestoneStatus.CANCELLED:
            raise ConflictError("Cannot achieve a cancelled milestone")

        milestone.status = MilestoneStatus.ACHIEVED
        milestone.actual_date = actual_date or date.today()

        if self.principal and hasattr(self.principal, "person_id"):
            milestone.updated_by_id = self.principal.person_id

        return milestone

    def mark_missed(self, milestone_id: uuid.UUID) -> Milestone:
        """Mark a milestone as missed."""
        milestone = self.get_milestone_or_raise(milestone_id)

        if milestone.status != MilestoneStatus.PENDING:
            raise ConflictError(
                f"Cannot mark milestone as missed from status {milestone.status.value}"
            )

        milestone.status = MilestoneStatus.MISSED

        if self.principal and hasattr(self.principal, "person_id"):
            milestone.updated_by_id = self.principal.person_id

        return milestone

    def cancel_milestone(self, milestone_id: uuid.UUID) -> Milestone:
        """Cancel a milestone."""
        milestone = self.get_milestone_or_raise(milestone_id)

        if milestone.status == MilestoneStatus.CANCELLED:
            raise ConflictError("Milestone is already cancelled")

        milestone.status = MilestoneStatus.CANCELLED

        if self.principal and hasattr(self.principal, "person_id"):
            milestone.updated_by_id = self.principal.person_id

        return milestone

    # =========================================================================
    # Metrics
    # =========================================================================

    def get_milestone_counts_by_status(
        self, project_id: uuid.UUID | None = None
    ) -> dict[MilestoneStatus, int]:
        """Get count of milestones grouped by status."""
        from sqlalchemy import func

        stmt = (
            select(Milestone.status, func.count(Milestone.milestone_id))
            .where(Milestone.organization_id == self.organization_id)
            .group_by(Milestone.status)
        )
        if project_id:
            stmt = stmt.where(Milestone.project_id == project_id)

        results = self.db.execute(stmt).all()
        return {status: count for status, count in results}
