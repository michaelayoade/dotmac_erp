"""
ERPNext Task Sync Service.

Syncs Task DocType to pm.task table with hierarchy support.
"""

import logging
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pm import Task, TaskPriority, TaskStatus
from app.models.sync import SyncEntity
from app.services.erpnext.mappings.tasks import TaskMapping

from .base import BaseSyncService

logger = logging.getLogger(__name__)


class TaskSyncService(BaseSyncService[Task]):
    """
    Sync service for ERPNext Tasks.

    Features:
    - Hierarchy support via parent_task
    - Project linkage resolution
    - Status and priority mapping
    - Progress tracking
    """

    source_doctype = "Task"
    target_table = "pm.task"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        super().__init__(db, organization_id, user_id)
        self.mapping = TaskMapping()
        self._project_cache: dict[str, uuid.UUID] = {}

    def fetch_records(self, client: Any, since: Optional[datetime] = None):
        """
        Fetch Task records from ERPNext.

        Tasks are fetched with optional project filter.
        """
        filters = []

        # Filter by modification date for incremental sync
        if since:
            filters.append(["modified", ">=", since.isoformat()])

            # Exclude template tasks
            filters.append(["is_template", "=", "0"])

        fields = [
            "name",
            "subject",
            "status",
            "priority",
            "project",
            "parent_task",
            "exp_start_date",
            "exp_end_date",
            "expected_time",
            "actual_time",
            "progress",
            "description",
            "completed_on",
            "modified",
        ]

        for record in client.get_all_documents("Task", filters=filters, fields=fields):
            yield record

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform ERPNext Task to DotMac task format."""
        return self.mapping.transform_record(record)

    def create_entity(self, data: dict[str, Any]) -> Task:
        """Create DotMac Task from transformed data."""
        # Resolve project ID
        project_source_name = data.pop("_project_source_name", None)
        project_id = self._resolve_project_id(project_source_name)

        if not project_id:
            raise ValueError(
                f"Cannot create task without project: {project_source_name}"
            )

        # Resolve parent task ID
        parent_source_name = data.pop("_parent_task_source_name", None)
        parent_task_id = self._resolve_task_id(parent_source_name)

        # Remove internal fields
        data.pop("_source_name", None)

        # Convert status and priority strings to enums
        status_value = data.pop("status", "OPEN")
        priority_value = data.pop("priority", "MEDIUM")

        task = Task(
            organization_id=self.organization_id,
            project_id=project_id,
            parent_task_id=parent_task_id,
            status=TaskStatus(status_value),
            priority=TaskPriority(priority_value),
            # Don't set created_by_id - synced data doesn't have a DotMac creator
            **data,
        )

        return task

    def update_entity(self, entity: Task, data: dict[str, Any]) -> Task:
        """Update existing Task with new data."""
        # Remove reference fields we don't update
        data.pop("_source_name", None)
        data.pop("_project_source_name", None)
        data.pop("_parent_task_source_name", None)

        # Convert status and priority strings to enums
        if "status" in data:
            entity.status = TaskStatus(data.pop("status"))
        if "priority" in data:
            entity.priority = TaskPriority(data.pop("priority"))

        # Update other fields
        for key, value in data.items():
            if hasattr(entity, key) and value is not None:
                setattr(entity, key, value)

        # Don't set updated_by_id - synced data doesn't have a DotMac updater

        return entity

    def get_entity_id(self, entity: Task) -> uuid.UUID:
        """Get the task ID."""
        return entity.task_id

    def find_existing_entity(self, source_name: str) -> Optional[Task]:
        """Find existing Task by sync record."""
        sync_entity = self.get_sync_entity(source_name)
        if not sync_entity or not sync_entity.target_id:
            return None

        return self.db.execute(
            select(Task).where(Task.task_id == sync_entity.target_id)
        ).scalar_one_or_none()

    def _resolve_project_id(
        self, project_source_name: Optional[str]
    ) -> Optional[uuid.UUID]:
        """Resolve DotMac project_id from ERPNext project name."""
        if not project_source_name:
            return None

        # Check cache
        if project_source_name in self._project_cache:
            return self._project_cache[project_source_name]

        # Look up in sync entities
        result = self.db.execute(
            select(SyncEntity.target_id).where(
                SyncEntity.organization_id == self.organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == "Project",
                SyncEntity.source_name == project_source_name,
            )
        ).scalar_one_or_none()

        if result:
            self._project_cache[project_source_name] = result

        return result

    def _resolve_task_id(self, task_source_name: Optional[str]) -> Optional[uuid.UUID]:
        """Resolve DotMac task_id from ERPNext task name."""
        if not task_source_name:
            return None

        # Look up in sync entities
        result = self.db.execute(
            select(SyncEntity.target_id).where(
                SyncEntity.organization_id == self.organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == "Task",
                SyncEntity.source_name == task_source_name,
            )
        ).scalar_one_or_none()

        return result
