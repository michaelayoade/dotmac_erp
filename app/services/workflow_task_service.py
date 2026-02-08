"""Service for managing workflow tasks."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.workflow_task import (
    WorkflowTask,
    WorkflowTaskPriority,
    WorkflowTaskStatus,
)
from app.services.common import PaginatedResult, PaginationParams

logger = logging.getLogger(__name__)

__all__ = ["WorkflowTaskService"]


class WorkflowTaskService:
    """Workflow task service."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def list_tasks(
        self,
        org_id: UUID,
        *,
        assignee_employee_id: UUID | None = None,
        status: WorkflowTaskStatus | None = None,
        priority: WorkflowTaskPriority | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[WorkflowTask]:
        query = select(WorkflowTask).where(WorkflowTask.organization_id == org_id)

        if assignee_employee_id:
            query = query.where(
                WorkflowTask.assignee_employee_id == assignee_employee_id
            )

        if status:
            query = query.where(WorkflowTask.status == status)

        if priority:
            query = query.where(WorkflowTask.priority == priority)

        query = query.order_by(WorkflowTask.created_at.desc())

        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_task(self, org_id: UUID, task_id: UUID) -> WorkflowTask:
        task = self.db.scalar(
            select(WorkflowTask).where(
                WorkflowTask.organization_id == org_id,
                WorkflowTask.task_id == task_id,
            )
        )
        if not task:
            raise ValueError("Task not found")
        return task

    def update_status(
        self,
        org_id: UUID,
        task_id: UUID,
        status: WorkflowTaskStatus,
    ) -> WorkflowTask:
        task = self.get_task(org_id, task_id)
        task.status = status
        self.db.flush()
        return task

    def complete_task(self, org_id: UUID, task_id: UUID) -> WorkflowTask:
        return self.update_status(org_id, task_id, WorkflowTaskStatus.COMPLETED)

    def snooze_task(
        self,
        org_id: UUID,
        task_id: UUID,
        *,
        days: int = 1,
    ) -> WorkflowTask:
        task = self.get_task(org_id, task_id)
        task.due_at = (task.due_at or datetime.utcnow()) + timedelta(days=days)
        self.db.flush()
        return task

    def summary(
        self,
        org_id: UUID,
        *,
        assignee_employee_id: UUID | None = None,
    ) -> dict:
        query = select(WorkflowTask.status, func.count(WorkflowTask.task_id)).where(
            WorkflowTask.organization_id == org_id
        )
        if assignee_employee_id:
            query = query.where(
                WorkflowTask.assignee_employee_id == assignee_employee_id
            )
        results = self.db.execute(query.group_by(WorkflowTask.status)).all()
        return {status.value: count for status, count in results}
