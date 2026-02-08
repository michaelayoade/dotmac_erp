"""
Dashboard Service - PM Module.

Business logic for project dashboards and reporting.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.finance.core_org.project import Project, ProjectStatus
from app.models.pm import (
    Milestone,
    MilestoneStatus,
    ResourceAllocation,
    Task,
    TaskStatus,
    TimeEntry,
)
from app.services.common import NotFoundError

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.auth import Principal

__all__ = ["DashboardService"]


class DashboardService:
    """
    Service for project dashboard and reporting.
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
    # Project Summary
    # =========================================================================

    def get_project_summary(self, project_id: uuid.UUID) -> dict:
        """Get high-level project summary."""
        project = self.db.scalars(
            select(Project).where(
                Project.project_id == project_id,
                Project.organization_id == self.organization_id,
            )
        ).first()

        if not project:
            raise NotFoundError(f"Project {project_id} not found")

        days_remaining = None
        if project.end_date:
            days_remaining = (project.end_date - date.today()).days

        return {
            "project_id": project.project_id,
            "project_code": project.project_code,
            "project_name": project.project_name,
            "status": project.status.value,
            "priority": project.project_priority.value
            if project.project_priority
            else "MEDIUM",
            "percent_complete": project.percent_complete,
            "start_date": project.start_date,
            "end_date": project.end_date,
            "budget_amount": project.budget_amount,
            "estimated_cost": project.estimated_cost,
            "actual_cost": project.actual_cost,
            "is_overdue": project.is_overdue,
            "days_remaining": days_remaining,
        }

    # =========================================================================
    # Task Metrics
    # =========================================================================

    def get_task_metrics(self, project_id: uuid.UUID) -> dict:
        """Get task statistics for a project."""
        base_where = and_(
            Task.project_id == project_id,
            Task.organization_id == self.organization_id,
            Task.is_deleted == False,  # noqa: E712
        )

        # Total tasks
        total_tasks = (
            self.db.scalar(select(func.count(Task.task_id)).where(base_where)) or 0
        )

        # Tasks by status
        status_counts = self.db.execute(
            select(Task.status, func.count(Task.task_id))
            .where(base_where)
            .group_by(Task.status)
        ).all()
        tasks_by_status = {s.value: c for s, c in status_counts}

        # Tasks by priority
        priority_counts = self.db.execute(
            select(Task.priority, func.count(Task.task_id))
            .where(base_where)
            .group_by(Task.priority)
        ).all()
        tasks_by_priority = {p.value: c for p, c in priority_counts}

        # Overdue tasks
        overdue_tasks = (
            self.db.scalar(
                select(func.count(Task.task_id)).where(
                    base_where,
                    Task.due_date < date.today(),
                    Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
                )
            )
            or 0
        )

        # Completion rate
        completed = tasks_by_status.get(TaskStatus.COMPLETED.value, 0)
        completion_rate = Decimal("0")
        if total_tasks > 0:
            completion_rate = (Decimal(completed) / Decimal(total_tasks)) * 100

        return {
            "project_id": project_id,
            "total_tasks": total_tasks,
            "open_tasks": tasks_by_status.get(TaskStatus.OPEN.value, 0),
            "in_progress_tasks": tasks_by_status.get(TaskStatus.IN_PROGRESS.value, 0),
            "completed_tasks": completed,
            "overdue_tasks": overdue_tasks,
            "tasks_by_status": tasks_by_status,
            "tasks_by_priority": tasks_by_priority,
            "completion_rate": completion_rate,
        }

    # =========================================================================
    # Budget Comparison
    # =========================================================================

    def get_budget_vs_actual(self, project_id: uuid.UUID) -> dict:
        """Get budget vs actual comparison for a project."""
        project = self.db.scalars(
            select(Project).where(
                Project.project_id == project_id,
                Project.organization_id == self.organization_id,
            )
        ).first()

        if not project:
            raise NotFoundError(f"Project {project_id} not found")

        budget = project.budget_amount
        estimated = project.estimated_cost
        actual = project.actual_cost or Decimal("0")

        remaining = None
        variance = None
        variance_percent = None

        if budget:
            remaining = budget - actual
            variance = budget - actual  # positive = under budget
            if budget > 0:
                variance_percent = (variance / budget) * 100

        return {
            "project_id": project_id,
            "project_name": project.project_name,
            "budget_amount": budget,
            "estimated_cost": estimated,
            "actual_cost": actual,
            "remaining_budget": remaining,
            "budget_variance": variance,
            "budget_variance_percent": variance_percent,
            "cost_to_complete": None,  # Would need task estimates
            "estimated_at_completion": None,  # Would need task estimates
        }

    # =========================================================================
    # Resource Utilization
    # =========================================================================

    def get_resource_utilization(self, project_id: uuid.UUID) -> dict:
        """Get resource utilization summary for a project."""
        # Get active team members
        team = self.db.scalars(
            select(ResourceAllocation).where(
                ResourceAllocation.project_id == project_id,
                ResourceAllocation.organization_id == self.organization_id,
                ResourceAllocation.is_active == True,  # noqa: E712
            )
        ).all()

        total_allocation = sum(m.allocation_percent for m in team)
        average_allocation = total_allocation / len(team) if team else Decimal("0")

        # Get hours logged
        total_hours = self.db.scalar(
            select(func.sum(TimeEntry.hours)).where(
                TimeEntry.project_id == project_id,
                TimeEntry.organization_id == self.organization_id,
            )
        ) or Decimal("0")

        billable_hours = self.db.scalar(
            select(func.sum(TimeEntry.hours)).where(
                TimeEntry.project_id == project_id,
                TimeEntry.organization_id == self.organization_id,
                TimeEntry.is_billable == True,  # noqa: E712
            )
        ) or Decimal("0")

        billable_percent = Decimal("0")
        if total_hours > 0:
            billable_percent = (billable_hours / total_hours) * 100

        return {
            "total_team_members": len(team),
            "active_allocations": len([m for m in team if m.is_active]),
            "total_allocated_percent": total_allocation,
            "average_allocation": average_allocation,
            "total_hours_logged": total_hours,
            "billable_hours": billable_hours,
            "billable_percent": billable_percent,
        }

    # =========================================================================
    # Milestone Summary
    # =========================================================================

    def get_milestone_summary(self, project_id: uuid.UUID) -> dict:
        """Get milestone summary for a project."""
        base_where = and_(
            Milestone.project_id == project_id,
            Milestone.organization_id == self.organization_id,
        )

        # Counts by status
        status_counts = self.db.execute(
            select(Milestone.status, func.count(Milestone.milestone_id))
            .where(base_where)
            .group_by(Milestone.status)
        ).all()
        counts = {s.value: c for s, c in status_counts}

        # Upcoming milestones (next 30 days)
        today = date.today()
        upcoming = self.db.scalars(
            select(Milestone)
            .where(
                base_where,
                Milestone.status == MilestoneStatus.PENDING,
                Milestone.target_date >= today,
                Milestone.target_date <= today + timedelta(days=30),
            )
            .order_by(Milestone.target_date)
            .limit(5)
        ).all()

        # Overdue milestones
        overdue = self.db.scalars(
            select(Milestone)
            .where(
                base_where,
                Milestone.status == MilestoneStatus.PENDING,
                Milestone.target_date < today,
            )
            .order_by(Milestone.target_date)
        ).all()

        return {
            "project_id": project_id,
            "total_milestones": sum(counts.values()),
            "achieved": counts.get(MilestoneStatus.ACHIEVED.value, 0),
            "pending": counts.get(MilestoneStatus.PENDING.value, 0),
            "missed": counts.get(MilestoneStatus.MISSED.value, 0),
            "upcoming": [
                {
                    "milestone_id": m.milestone_id,
                    "milestone_name": m.milestone_name,
                    "target_date": m.target_date,
                    "days_until": (m.target_date - today).days,
                }
                for m in upcoming
            ],
            "overdue": [
                {
                    "milestone_id": m.milestone_id,
                    "milestone_name": m.milestone_name,
                    "target_date": m.target_date,
                    "days_overdue": (today - m.target_date).days,
                    "status": m.status.value,
                }
                for m in overdue
            ],
        }

    # =========================================================================
    # Full Dashboard
    # =========================================================================

    def get_project_dashboard(self, project_id: uuid.UUID) -> dict:
        """Get complete dashboard data for a project."""
        return {
            "summary": self.get_project_summary(project_id),
            "task_metrics": self.get_task_metrics(project_id),
            "budget": self.get_budget_vs_actual(project_id),
            "resources": self.get_resource_utilization(project_id),
            "milestones": self.get_milestone_summary(project_id),
        }

    # =========================================================================
    # Organization-wide Dashboards
    # =========================================================================

    def get_projects_overview(self) -> dict:
        """Get overview of all projects in the organization."""
        base_where = Project.organization_id == self.organization_id

        # Total projects
        total = (
            self.db.scalar(select(func.count(Project.project_id)).where(base_where))
            or 0
        )

        # Projects by status
        status_counts = self.db.execute(
            select(Project.status, func.count(Project.project_id))
            .where(base_where)
            .group_by(Project.status)
        ).all()
        by_status = {s.value: c for s, c in status_counts}

        # Active projects with overdue tasks
        # This is a simplified version - would need a subquery for accurate count
        active_project_ids = self.db.scalars(
            select(Project.project_id).where(
                base_where,
                Project.status == ProjectStatus.ACTIVE,
            )
        ).all()

        projects_with_overdue = 0
        for pid in active_project_ids:
            has_overdue = self.db.scalar(
                select(func.count(Task.task_id)).where(
                    Task.project_id == pid,
                    Task.is_deleted == False,  # noqa: E712
                    Task.due_date < date.today(),
                    Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
                )
            )
            if has_overdue and has_overdue > 0:
                projects_with_overdue += 1

        return {
            "total_projects": total,
            "by_status": by_status,
            "active": by_status.get(ProjectStatus.ACTIVE.value, 0),
            "completed": by_status.get(ProjectStatus.COMPLETED.value, 0),
            "on_hold": by_status.get(ProjectStatus.ON_HOLD.value, 0),
            "projects_with_overdue_tasks": projects_with_overdue,
        }
