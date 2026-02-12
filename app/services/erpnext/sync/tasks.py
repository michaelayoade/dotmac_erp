"""
ERPNext Task Sync Service.

Syncs Task DocType to pm.task table with hierarchy, dependency,
and assignment support.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pm import Task, TaskPriority, TaskStatus
from app.models.pm.task_dependency import DependencyType, TaskDependency
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
    - Task dependency sync (Task Depends On child table)
    - Assignment sync (_assign JSON field)
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
        self._employee_cache: dict[str, uuid.UUID | None] = {}

    def fetch_records(self, client: Any, since: datetime | None = None):
        """
        Fetch Task records from ERPNext.

        Fetches _assign field for assignee resolution and Task Depends On
        child table entries per task for dependency sync.
        """
        filters: list[list[str]] = []

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
            "_assign",
            "modified",
        ]

        for task in client.get_all_documents("Task", filters=filters, fields=fields):
            # Fetch dependencies (child table) per task
            task_name = task.get("name", "")
            if task_name:
                task["_depends_on"] = client.get_task_dependencies(task_name)
            else:
                task["_depends_on"] = []

            yield task

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

        # Extract assignment and dependency data before model creation
        assign_raw = data.pop("_assign_raw", None)
        depends_on = data.pop("_depends_on", None)

        # Remove internal fields
        data.pop("_source_name", None)

        # Resolve assigned_to from _assign JSON
        assigned_to_id = self._resolve_assigned_to(assign_raw)

        # Convert status and priority strings to enums
        status_value = data.pop("status", "OPEN")
        priority_value = data.pop("priority", "MEDIUM")

        task = Task(
            organization_id=self.organization_id,
            project_id=project_id,
            parent_task_id=parent_task_id,
            assigned_to_id=assigned_to_id,
            status=TaskStatus(status_value),
            priority=TaskPriority(priority_value),
            # Don't set created_by_id - synced data doesn't have a DotMac creator
            **data,
        )

        # Flush to get task_id before creating dependencies
        self.db.add(task)
        self.db.flush()

        # Sync dependencies
        if depends_on:
            self._sync_dependencies(task, depends_on)

        return task

    def update_entity(self, entity: Task, data: dict[str, Any]) -> Task:
        """Update existing Task with new data."""
        # Remove reference fields we don't update
        data.pop("_source_name", None)
        data.pop("_project_source_name", None)
        data.pop("_parent_task_source_name", None)

        # Extract assignment and dependency data
        assign_raw = data.pop("_assign_raw", None)
        depends_on = data.pop("_depends_on", None)

        # Convert status and priority strings to enums
        if "status" in data:
            entity.status = TaskStatus(data.pop("status"))
        if "priority" in data:
            entity.priority = TaskPriority(data.pop("priority"))

        # Update assigned_to from _assign JSON
        assigned_to_id = self._resolve_assigned_to(assign_raw)
        if assigned_to_id is not None:
            entity.assigned_to_id = assigned_to_id

        # Update other fields
        for key, value in data.items():
            if hasattr(entity, key) and value is not None:
                setattr(entity, key, value)

        # Re-sync dependencies: delete old, recreate from current ERPNext data
        if depends_on is not None:
            self._sync_dependencies(entity, depends_on, replace=True)

        # Don't set updated_by_id - synced data doesn't have a DotMac updater

        return entity

    def get_entity_id(self, entity: Task) -> uuid.UUID:
        """Get the task ID."""
        return entity.task_id

    def find_existing_entity(self, source_name: str) -> Task | None:
        """Find existing Task by sync record."""
        sync_entity = self.get_sync_entity(source_name)
        if not sync_entity or not sync_entity.target_id:
            return None

        return self.db.execute(
            select(Task).where(Task.task_id == sync_entity.target_id)
        ).scalar_one_or_none()

    def _resolve_project_id(self, project_source_name: str | None) -> uuid.UUID | None:
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

    def _resolve_task_id(self, task_source_name: str | None) -> uuid.UUID | None:
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

    def _resolve_employee_by_email(self, email: str | None) -> uuid.UUID | None:
        """
        Resolve employee ID from email address.

        Checks Person.email (work email) and Employee.personal_email.
        """
        if not email:
            return None

        email_lower = email.strip().lower()

        # Check cache
        if email_lower in self._employee_cache:
            return self._employee_cache[email_lower]

        from sqlalchemy import func, or_

        from app.models.people.hr import Employee
        from app.models.person import Person

        result = self.db.execute(
            select(Employee)
            .join(Person, Person.id == Employee.person_id)
            .where(
                Employee.organization_id == self.organization_id,
                or_(
                    func.lower(Person.email) == email_lower,
                    func.lower(Employee.personal_email) == email_lower,
                ),
            )
        ).scalar_one_or_none()

        employee_id = result.employee_id if result else None
        self._employee_cache[email_lower] = employee_id
        return employee_id

    def _resolve_assigned_to(self, assign_raw: Any) -> uuid.UUID | None:
        """
        Resolve assigned_to employee from ERPNext _assign JSON field.

        The _assign field is a JSON string containing an array of email
        addresses, e.g. '["user@example.com"]'. We take the first email
        and resolve to a DotMac employee.
        """
        if not assign_raw:
            return None

        # Parse _assign JSON — it may already be a list or a JSON string
        emails: list[str] = []
        if isinstance(assign_raw, list):
            emails = assign_raw
        elif isinstance(assign_raw, str):
            try:
                parsed = json.loads(assign_raw)
                if isinstance(parsed, list):
                    emails = parsed
            except (json.JSONDecodeError, ValueError):
                logger.debug("Could not parse _assign field: %s", assign_raw[:100])
                return None

        if not emails:
            return None

        # Resolve first assignee email to employee
        first_email = str(emails[0]).strip()
        if not first_email:
            return None

        employee_id = self._resolve_employee_by_email(first_email)
        if not employee_id:
            logger.debug(
                "Could not resolve assigned_to employee for email '%s'", first_email
            )
        return employee_id

    def _sync_dependencies(
        self,
        task: Task,
        depends_on: list[dict[str, Any]],
        *,
        replace: bool = False,
    ) -> int:
        """
        Sync task dependencies from ERPNext Task Depends On child table.

        Args:
            task: The dependent DotMac task
            depends_on: List of dependency records from ERPNext
                        (each has 'task' field with predecessor name)
            replace: If True, delete existing dependencies first (for updates)

        Returns:
            Number of dependencies created
        """
        if replace:
            # Delete existing dependencies for this task
            existing = (
                self.db.execute(
                    select(TaskDependency).where(TaskDependency.task_id == task.task_id)
                )
                .scalars()
                .all()
            )
            for dep in existing:
                self.db.delete(dep)
            self.db.flush()

        created = 0
        seen_predecessors: set[uuid.UUID] = set()

        for dep_record in depends_on:
            predecessor_name = dep_record.get("task")
            if not predecessor_name:
                continue

            predecessor_id = self._resolve_task_id(str(predecessor_name))
            if not predecessor_id:
                logger.debug(
                    "Could not resolve dependency predecessor '%s' for task %s",
                    predecessor_name,
                    task.task_id,
                )
                continue

            # Skip self-references
            if predecessor_id == task.task_id:
                logger.warning(
                    "Skipping self-referencing dependency for task %s", task.task_id
                )
                continue

            # Skip duplicates within this batch
            if predecessor_id in seen_predecessors:
                continue
            seen_predecessors.add(predecessor_id)

            dependency = TaskDependency(
                task_id=task.task_id,
                depends_on_task_id=predecessor_id,
                dependency_type=DependencyType.FINISH_TO_START,
            )
            self.db.add(dependency)
            created += 1

        if created:
            self.db.flush()
            logger.debug("Created %d dependencies for task %s", created, task.task_id)

        return created
