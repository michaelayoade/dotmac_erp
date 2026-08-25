"""Project and task observations accepted from Dotmac Sub."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select

from app.models.finance.core_org.project import Project, ProjectStatus
from app.models.pm.task import Task, TaskStatus
from app.models.sync.sync_entity import SyncEntity
from app.schemas.sync.dotmac_sub import (
    SubProjectPayload,
    SubProjectTaskPayload,
    SubWorkOrderPayload,
)
from app.services.sync.sub.base import (
    SUB_PROJECT,
    SUB_PROJECT_TASK,
    SUB_WORK_ORDER,
    _SubSyncBase,
)
from app.services.sync.sub_mappings import (
    PROJECT_STATUS_MAP,
    TASK_STATUS_MAP,
    map_project_type,
    map_task_priority,
)

logger = logging.getLogger(__name__)


class _ProjectSyncMixin(_SubSyncBase):
    def sync_project(
        self, organization_id: UUID, data: SubProjectPayload
    ) -> SyncEntity:
        sync = self._get_sync(organization_id, SUB_PROJECT, data.source_id)
        project = (
            self.db.get(Project, sync.target_id) if sync and sync.target_id else None
        )
        if project is None:
            project = self._create_project(organization_id, data)
            self.db.flush()
        else:
            self._update_project(project, data)
        result = self._record_sync(
            organization_id,
            SUB_PROJECT,
            data.source_id,
            target_table="core_org.project",
            target_id=project.project_id,
        )
        logger.info(
            "Projected Sub project %s -> %s", data.source_id, project.project_id
        )
        return result

    def sync_project_task(
        self, organization_id: UUID, data: SubProjectTaskPayload
    ) -> SyncEntity:
        project_id = self._resolve_project_id(organization_id, data.project_source_id)
        if project_id is None:
            raise ValueError(
                f"project source mapping not found: {data.project_source_id}"
            )
        parent_task_id = self._resolve_project_task_id(
            organization_id, data.parent_task_source_id
        )
        if data.parent_task_source_id and parent_task_id is None:
            raise ValueError(
                f"parent task source mapping not found: {data.parent_task_source_id}"
            )
        sync = self._get_sync(organization_id, SUB_PROJECT_TASK, data.source_id)
        task = self.db.get(Task, sync.target_id) if sync and sync.target_id else None
        if task is None:
            task = Task(
                organization_id=organization_id,
                project_id=project_id,
                task_code=self._generate_unique_code("PT", data.source_id, max_len=30),
                task_name=data.title,
            )
            self.db.add(task)
            self.db.flush()
        task.project_id = project_id
        task.parent_task_id = parent_task_id
        task.ticket_id = None
        task.task_name = data.title
        task.description = data.description
        task.status = TASK_STATUS_MAP.get(data.status.lower(), TaskStatus.OPEN)
        task.priority = map_task_priority(data.priority)
        task.start_date = data.start_at.date() if data.start_at else None
        task.due_date = data.due_at.date() if data.due_at else None
        task.actual_end_date = data.completed_at.date() if data.completed_at else None
        task.estimated_hours = data.effort_hours
        task.progress_percent = 100 if task.status == TaskStatus.COMPLETED else 0
        return self._record_sync(
            organization_id,
            SUB_PROJECT_TASK,
            data.source_id,
            target_table="pm.task",
            target_id=task.task_id,
        )

    def sync_work_order(
        self, organization_id: UUID, data: SubWorkOrderPayload
    ) -> SyncEntity:
        project_id = self._resolve_project_id(
            organization_id, data.project_source_id
        ) or self._get_or_create_default_project(organization_id)
        assignee_email = data.assigned_employee_email or next(
            iter(data.assigned_employee_emails), None
        )
        employee_id = self._resolve_employee_id(organization_id, assignee_email)
        sync = self._get_sync(organization_id, SUB_WORK_ORDER, data.source_id)
        task = self.db.get(Task, sync.target_id) if sync and sync.target_id else None
        if task is None:
            task = Task(
                organization_id=organization_id,
                project_id=project_id,
                task_code=self._generate_unique_code("WO", data.source_id, max_len=30),
                task_name=data.title,
            )
            self.db.add(task)
            self.db.flush()
        task.project_id = project_id
        task.ticket_id = None
        task.task_name = data.title
        task.status = TASK_STATUS_MAP.get(data.status.lower(), TaskStatus.OPEN)
        task.priority = map_task_priority(data.priority)
        task.assigned_to_id = employee_id
        task.start_date = data.scheduled_start.date() if data.scheduled_start else None
        task.due_date = data.scheduled_end.date() if data.scheduled_end else None
        task.progress_percent = 100 if task.status == TaskStatus.COMPLETED else 0
        return self._record_sync(
            organization_id,
            SUB_WORK_ORDER,
            data.source_id,
            target_table="pm.task",
            target_id=task.task_id,
        )

    def _create_project(
        self, organization_id: UUID, data: SubProjectPayload
    ) -> Project:
        source_code = (data.code or "").strip()
        code_in_use = (
            self.db.scalar(
                select(Project.project_id).where(
                    Project.organization_id == organization_id,
                    Project.project_code == source_code,
                )
            )
            if source_code and len(source_code) <= 20
            else None
        )
        project = Project(
            organization_id=organization_id,
            project_code=(
                source_code
                if source_code and len(source_code) <= 20 and code_in_use is None
                else self._generate_unique_code("SUB", data.source_id, max_len=20)
            ),
            project_name=data.name,
            description=data.description,
            status=PROJECT_STATUS_MAP.get(data.status.lower(), ProjectStatus.ACTIVE),
            project_type=map_project_type(data.project_type),
            start_date=data.start_at.date() if data.start_at else None,
            end_date=data.due_at.date() if data.due_at else None,
        )
        self.db.add(project)
        return project

    @staticmethod
    def _update_project(project: Project, data: SubProjectPayload) -> None:
        project.project_name = data.name
        project.description = data.description
        project.status = PROJECT_STATUS_MAP.get(
            data.status.lower(), ProjectStatus.ACTIVE
        )
        project.project_type = map_project_type(data.project_type)
        project.start_date = data.start_at.date() if data.start_at else None
        project.end_date = data.due_at.date() if data.due_at else None
