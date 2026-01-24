"""
Gantt Chart Service - PM Module.

Business logic for generating Gantt chart data.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import TYPE_CHECKING, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.finance.core_org.project import Project
from app.models.pm import Milestone, MilestoneStatus, Task, TaskDependency
from app.schemas.pm.gantt import (
    GanttChartData,
    GanttLink,
    GanttTask,
    dependency_type_to_gantt_type,
)
from app.services.common import NotFoundError

if TYPE_CHECKING:
    from app.auth import Principal

__all__ = ["GanttService"]


class GanttService:
    """
    Service for generating Gantt chart data.
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

    def get_gantt_data(self, project_id: uuid.UUID) -> GanttChartData:
        """Generate Gantt chart data for a project."""
        # Get project
        project = self.db.scalars(
            select(Project).where(
                Project.project_id == project_id,
                Project.organization_id == self.organization_id,
            )
        ).first()

        if not project:
            raise NotFoundError(f"Project {project_id} not found")

        # Get all tasks
        tasks = self.db.scalars(
            select(Task)
            .where(
                Task.project_id == project_id,
                Task.organization_id == self.organization_id,
                Task.is_deleted == False,  # noqa: E712
            )
            .options(
                selectinload(Task.assigned_to),
                selectinload(Task.dependencies),
            )
            .order_by(Task.start_date.asc().nullslast(), Task.task_code)
        ).all()

        # Get milestones
        milestones = self.db.scalars(
            select(Milestone)
            .where(
                Milestone.project_id == project_id,
                Milestone.organization_id == self.organization_id,
            )
            .order_by(Milestone.target_date)
        ).all()

        # Convert tasks to Gantt format
        gantt_tasks: List[GanttTask] = []
        gantt_links: List[GanttLink] = []

        for task in tasks:
            # Calculate duration
            duration = None
            end_date = task.due_date
            if task.start_date and task.due_date:
                duration = (task.due_date - task.start_date).days + 1
            elif task.start_date and task.estimated_hours:
                # Estimate end date from hours (8 hours/day)
                days = int(task.estimated_hours / 8) + 1
                duration = days
                end_date = task.start_date + timedelta(days=days - 1)

            # Get assignee name
            assigned_to = None
            if task.assigned_to:
                assigned_to = getattr(task.assigned_to, "full_name", None)

            gantt_task = GanttTask(
                id=str(task.task_id),
                text=task.task_name,
                start_date=task.start_date,
                end_date=end_date,
                duration=duration,
                progress=task.progress_percent / 100.0 if task.progress_percent else 0.0,
                parent=str(task.parent_task_id) if task.parent_task_id else None,
                type="task",
                priority=task.priority,
                status=task.status,
                assigned_to=assigned_to,
                project_id=str(project_id),
                open=True,
            )
            gantt_tasks.append(gantt_task)

            # Add dependency links
            for dep in task.dependencies:
                gantt_link = GanttLink(
                    id=str(dep.dependency_id),
                    source=str(dep.depends_on_task_id),
                    target=str(task.task_id),
                    type=dependency_type_to_gantt_type(dep.dependency_type),
                    lag=dep.lag_days,
                )
                gantt_links.append(gantt_link)

        # Add milestones as special tasks
        for milestone in milestones:
            gantt_task = GanttTask(
                id=f"milestone_{milestone.milestone_id}",
                text=milestone.milestone_name,
                start_date=milestone.target_date,
                end_date=milestone.target_date,
                duration=0,
                progress=1.0 if milestone.status == MilestoneStatus.ACHIEVED else 0.0,
                parent=None,
                type="milestone",
                priority=None,
                status=None,
                assigned_to=None,
                project_id=str(project_id),
                open=True,
            )
            gantt_tasks.append(gantt_task)

            # If milestone is linked to a task, add a link
            if milestone.linked_task_id:
                gantt_link = GanttLink(
                    id=f"milestone_link_{milestone.milestone_id}",
                    source=str(milestone.linked_task_id),
                    target=f"milestone_{milestone.milestone_id}",
                    type="0",  # Finish to Start
                    lag=0,
                )
                gantt_links.append(gantt_link)

        return GanttChartData(
            project_id=project_id,
            project_name=project.project_name,
            start_date=project.start_date,
            end_date=project.end_date,
            tasks=gantt_tasks,
            links=gantt_links,
        )

    def get_multi_project_gantt(
        self, project_ids: List[uuid.UUID]
    ) -> List[GanttChartData]:
        """Generate Gantt chart data for multiple projects."""
        return [self.get_gantt_data(pid) for pid in project_ids]
