"""
Task Service - PM Module.

Business logic for task management including CRUD, dependencies,
status transitions, and hierarchy management.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Dict, List, Optional, Set

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, selectinload

from app.models.pm import (
    DependencyType,
    Task,
    TaskDependency,
    TaskPriority,
    TaskStatus,
)
from app.services.common import (
    ConflictError,
    NotFoundError,
    PaginatedResult,
    PaginationParams,
    ValidationError,
    paginate,
)

if TYPE_CHECKING:
    from app.auth import Principal

__all__ = ["TaskService"]


class TaskService:
    """
    Service for PM Task business logic.

    All mutation methods do NOT commit. Caller (route handler) is responsible
    for calling db.commit() after operation succeeds.
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

    def get_task(self, task_id: uuid.UUID) -> Optional[Task]:
        """Fetch a single task by ID."""
        stmt = (
            select(Task)
            .where(
                Task.task_id == task_id,
                Task.organization_id == self.organization_id,
                Task.is_deleted == False,  # noqa: E712
            )
            .options(
                selectinload(Task.assigned_to),
                selectinload(Task.project),
                selectinload(Task.subtasks),
            )
        )
        return self.db.scalars(stmt).first()

    def get_task_or_raise(self, task_id: uuid.UUID) -> Task:
        """Fetch a task or raise NotFoundError."""
        task = self.get_task(task_id)
        if not task:
            raise NotFoundError(f"Task {task_id} not found")
        return task

    def list_tasks(
        self,
        project_id: Optional[uuid.UUID] = None,
        status: Optional[TaskStatus] = None,
        priority: Optional[TaskPriority] = None,
        assigned_to_id: Optional[uuid.UUID] = None,
        parent_task_id: Optional[uuid.UUID] = None,
        include_subtasks: bool = True,
        params: Optional[PaginationParams] = None,
    ) -> PaginatedResult[Task]:
        """List tasks with filtering and pagination."""
        stmt = (
            select(Task)
            .where(
                Task.organization_id == self.organization_id,
                Task.is_deleted == False,  # noqa: E712
            )
            .options(
                selectinload(Task.assigned_to),
                selectinload(Task.project),
            )
            .order_by(Task.due_date.asc().nullslast(), Task.created_at.desc())
        )

        if project_id:
            stmt = stmt.where(Task.project_id == project_id)
        if status:
            stmt = stmt.where(Task.status == status)
        if priority:
            stmt = stmt.where(Task.priority == priority)
        if assigned_to_id:
            stmt = stmt.where(Task.assigned_to_id == assigned_to_id)
        if parent_task_id is not None:
            stmt = stmt.where(Task.parent_task_id == parent_task_id)
        elif not include_subtasks:
            # Only top-level tasks
            stmt = stmt.where(Task.parent_task_id.is_(None))

        return paginate(self.db, stmt, params)

    def get_subtasks(self, task_id: uuid.UUID) -> List[Task]:
        """Get all subtasks of a task."""
        stmt = (
            select(Task)
            .where(
                Task.parent_task_id == task_id,
                Task.organization_id == self.organization_id,
                Task.is_deleted == False,  # noqa: E712
            )
            .order_by(Task.task_code)
        )
        return list(self.db.scalars(stmt).all())

    def get_project_tasks(self, project_id: uuid.UUID) -> List[Task]:
        """Get all tasks for a project (for Gantt chart, etc.)."""
        stmt = (
            select(Task)
            .where(
                Task.project_id == project_id,
                Task.organization_id == self.organization_id,
                Task.is_deleted == False,  # noqa: E712
            )
            .options(
                selectinload(Task.dependencies),
                selectinload(Task.assigned_to),
            )
            .order_by(Task.start_date.asc().nullslast(), Task.task_code)
        )
        return list(self.db.scalars(stmt).all())

    def get_overdue_tasks(self, project_id: Optional[uuid.UUID] = None) -> List[Task]:
        """Get tasks that are past due date and not completed."""
        stmt = (
            select(Task)
            .where(
                Task.organization_id == self.organization_id,
                Task.is_deleted == False,  # noqa: E712
                Task.due_date < date.today(),
                Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
            )
            .order_by(Task.due_date)
        )
        if project_id:
            stmt = stmt.where(Task.project_id == project_id)
        return list(self.db.scalars(stmt).all())

    # =========================================================================
    # Write Operations
    # =========================================================================

    def create_task(self, data: Dict) -> Task:
        """
        Create a new task.

        Args:
            data: Dict containing task fields.

        Returns:
            Created Task instance (not committed).
        """
        task = Task(
            organization_id=self.organization_id,
            project_id=data["project_id"],
            task_code=data["task_code"],
            task_name=data["task_name"],
            description=data.get("description"),
            parent_task_id=data.get("parent_task_id"),
            ticket_id=data.get("ticket_id"),
            priority=data.get("priority", TaskPriority.MEDIUM),
            assigned_to_id=data.get("assigned_to_id"),
            start_date=data.get("start_date"),
            due_date=data.get("due_date"),
            estimated_hours=data.get("estimated_hours"),
        )

        if self.principal and hasattr(self.principal, "person_id"):
            task.created_by_id = self.principal.person_id

        self.db.add(task)
        self.db.flush()
        return task

    def update_task(self, task_id: uuid.UUID, data: Dict) -> Task:
        """
        Update an existing task.

        Args:
            task_id: Task UUID.
            data: Dict containing fields to update.

        Returns:
            Updated Task instance (not committed).
        """
        task = self.get_task_or_raise(task_id)

        updatable_fields = [
            "task_code",
            "task_name",
            "description",
            "parent_task_id",
            "ticket_id",
            "priority",
            "status",
            "assigned_to_id",
            "start_date",
            "due_date",
            "estimated_hours",
            "actual_hours",
            "progress_percent",
        ]

        for field in updatable_fields:
            if field in data and data[field] is not None:
                setattr(task, field, data[field])

        if self.principal and hasattr(self.principal, "person_id"):
            task.updated_by_id = self.principal.person_id

        return task

    def delete_task(self, task_id: uuid.UUID) -> bool:
        """
        Soft delete a task.

        Also soft-deletes all subtasks.

        Returns:
            True if deleted successfully.
        """
        task = self.get_task_or_raise(task_id)

        # Delete subtasks first
        for subtask in self.get_subtasks(task_id):
            self.delete_task(subtask.task_id)

        task.is_deleted = True
        if self.principal and hasattr(self.principal, "person_id"):
            task.deleted_by_id = self.principal.person_id

        return True

    def move_task(
        self, task_id: uuid.UUID, new_parent_id: Optional[uuid.UUID]
    ) -> Task:
        """Move a task to a new parent (or make it top-level)."""
        task = self.get_task_or_raise(task_id)

        if new_parent_id:
            # Validate new parent exists and is in same project
            new_parent = self.get_task_or_raise(new_parent_id)
            if new_parent.project_id != task.project_id:
                raise ValidationError("Cannot move task to parent in different project")
            # Check for circular reference
            if self._would_create_cycle(task_id, new_parent_id):
                raise ValidationError("Cannot create circular task hierarchy")

        task.parent_task_id = new_parent_id
        return task

    # =========================================================================
    # Status Operations
    # =========================================================================

    def start_task(self, task_id: uuid.UUID) -> Task:
        """Start a task (set to IN_PROGRESS)."""
        task = self.get_task_or_raise(task_id)

        if task.status not in (TaskStatus.OPEN, TaskStatus.ON_HOLD):
            raise ConflictError(
                f"Cannot start task in status {task.status.value}"
            )

        # Check dependencies are completed
        for dep in task.dependencies:
            if dep.depends_on_task.status != TaskStatus.COMPLETED:
                raise ConflictError(
                    f"Cannot start task: dependency {dep.depends_on_task.task_code} not completed"
                )

        task.status = TaskStatus.IN_PROGRESS
        if not task.actual_start_date:
            task.actual_start_date = date.today()

        return task

    def complete_task(self, task_id: uuid.UUID) -> Task:
        """Complete a task."""
        task = self.get_task_or_raise(task_id)

        if task.status == TaskStatus.COMPLETED:
            raise ConflictError("Task is already completed")
        if task.status == TaskStatus.CANCELLED:
            raise ConflictError("Cannot complete a cancelled task")

        # Check all subtasks are completed
        subtasks = self.get_subtasks(task_id)
        for subtask in subtasks:
            if subtask.status not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
                raise ConflictError(
                    f"Cannot complete task: subtask {subtask.task_code} not completed"
                )

        task.status = TaskStatus.COMPLETED
        task.actual_end_date = date.today()
        task.progress_percent = 100

        return task

    def update_progress(self, task_id: uuid.UUID, percent: int) -> Task:
        """Update task progress percentage."""
        if percent < 0 or percent > 100:
            raise ValidationError("Progress must be between 0 and 100")

        task = self.get_task_or_raise(task_id)
        task.progress_percent = percent

        # Auto-transition status based on progress
        if percent > 0 and task.status == TaskStatus.OPEN:
            task.status = TaskStatus.IN_PROGRESS
            if not task.actual_start_date:
                task.actual_start_date = date.today()
        elif percent == 100:
            task.status = TaskStatus.COMPLETED
            if not task.actual_end_date:
                task.actual_end_date = date.today()

        return task

    def assign_task(
        self, task_id: uuid.UUID, employee_id: Optional[uuid.UUID]
    ) -> Task:
        """Assign task to an employee (or unassign if None)."""
        task = self.get_task_or_raise(task_id)
        task.assigned_to_id = employee_id
        return task

    # =========================================================================
    # Dependency Operations
    # =========================================================================

    def add_dependency(
        self,
        task_id: uuid.UUID,
        depends_on_id: uuid.UUID,
        dependency_type: DependencyType = DependencyType.FINISH_TO_START,
        lag_days: int = 0,
    ) -> TaskDependency:
        """Add a dependency between tasks."""
        task = self.get_task_or_raise(task_id)
        depends_on = self.get_task_or_raise(depends_on_id)

        if task.project_id != depends_on.project_id:
            raise ValidationError("Dependencies must be within the same project")

        if task_id == depends_on_id:
            raise ValidationError("Task cannot depend on itself")

        # Check for existing dependency
        existing = self.db.scalars(
            select(TaskDependency).where(
                TaskDependency.task_id == task_id,
                TaskDependency.depends_on_task_id == depends_on_id,
            )
        ).first()
        if existing:
            raise ConflictError("Dependency already exists")

        # Check for cycle
        if self._would_create_dependency_cycle(task_id, depends_on_id):
            raise ValidationError("Cannot create circular dependency")

        dependency = TaskDependency(
            task_id=task_id,
            depends_on_task_id=depends_on_id,
            dependency_type=dependency_type,
            lag_days=lag_days,
        )
        self.db.add(dependency)
        self.db.flush()

        return dependency

    def remove_dependency(
        self, task_id: uuid.UUID, depends_on_id: uuid.UUID
    ) -> bool:
        """Remove a dependency between tasks."""
        dep = self.db.scalars(
            select(TaskDependency).where(
                TaskDependency.task_id == task_id,
                TaskDependency.depends_on_task_id == depends_on_id,
            )
        ).first()

        if not dep:
            raise NotFoundError("Dependency not found")

        self.db.delete(dep)
        return True

    def get_dependencies(self, task_id: uuid.UUID) -> List[TaskDependency]:
        """Get all dependencies of a task (tasks this task depends on)."""
        stmt = (
            select(TaskDependency)
            .where(TaskDependency.task_id == task_id)
            .options(selectinload(TaskDependency.depends_on_task))
        )
        return list(self.db.scalars(stmt).all())

    def get_dependents(self, task_id: uuid.UUID) -> List[TaskDependency]:
        """Get all tasks that depend on this task."""
        stmt = (
            select(TaskDependency)
            .where(TaskDependency.depends_on_task_id == task_id)
            .options(selectinload(TaskDependency.task))
        )
        return list(self.db.scalars(stmt).all())

    # =========================================================================
    # Metrics
    # =========================================================================

    def get_task_counts_by_status(
        self, project_id: Optional[uuid.UUID] = None
    ) -> Dict[TaskStatus, int]:
        """Get count of tasks grouped by status."""
        stmt = (
            select(Task.status, func.count(Task.task_id))
            .where(
                Task.organization_id == self.organization_id,
                Task.is_deleted == False,  # noqa: E712
            )
            .group_by(Task.status)
        )
        if project_id:
            stmt = stmt.where(Task.project_id == project_id)

        results = self.db.execute(stmt).all()
        return {status: count for status, count in results}

    def get_task_counts_by_priority(
        self, project_id: Optional[uuid.UUID] = None
    ) -> Dict[TaskPriority, int]:
        """Get count of tasks grouped by priority."""
        stmt = (
            select(Task.priority, func.count(Task.task_id))
            .where(
                Task.organization_id == self.organization_id,
                Task.is_deleted == False,  # noqa: E712
            )
            .group_by(Task.priority)
        )
        if project_id:
            stmt = stmt.where(Task.project_id == project_id)

        results = self.db.execute(stmt).all()
        return {priority: count for priority, count in results}

    # =========================================================================
    # Private Helpers
    # =========================================================================

    def _would_create_cycle(
        self, task_id: uuid.UUID, new_parent_id: uuid.UUID
    ) -> bool:
        """Check if moving task under new_parent would create a cycle."""
        current = new_parent_id
        while current:
            if current == task_id:
                return True
            parent = self.db.scalars(
                select(Task.parent_task_id).where(Task.task_id == current)
            ).first()
            current = parent
        return False

    def _would_create_dependency_cycle(
        self, task_id: uuid.UUID, depends_on_id: uuid.UUID
    ) -> bool:
        """Check if adding this dependency would create a cycle."""
        visited: Set[uuid.UUID] = set()
        to_visit = [depends_on_id]

        while to_visit:
            current = to_visit.pop()
            if current == task_id:
                return True
            if current in visited:
                continue
            visited.add(current)

            # Get dependencies of current
            deps = self.db.scalars(
                select(TaskDependency.depends_on_task_id).where(
                    TaskDependency.task_id == current
                )
            ).all()
            to_visit.extend(deps)

        return False
