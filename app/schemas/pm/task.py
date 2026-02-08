"""
Task Pydantic Schemas.

Schemas for PM Task API endpoints.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.pm import TaskPriority, TaskStatus

# =============================================================================
# Task Schemas
# =============================================================================


class TaskBase(BaseModel):
    """Base task schema."""

    task_code: str = Field(max_length=30)
    task_name: str = Field(max_length=200)
    description: str | None = None
    project_id: UUID
    parent_task_id: UUID | None = None
    priority: TaskPriority = TaskPriority.MEDIUM
    assigned_to_id: UUID | None = None
    start_date: date | None = None
    due_date: date | None = None
    estimated_hours: Decimal | None = None


class TaskCreate(TaskBase):
    """Create task request."""

    pass


class TaskUpdate(BaseModel):
    """Update task request."""

    task_code: str | None = Field(default=None, max_length=30)
    task_name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    parent_task_id: UUID | None = None
    priority: TaskPriority | None = None
    status: TaskStatus | None = None
    assigned_to_id: UUID | None = None
    start_date: date | None = None
    due_date: date | None = None
    estimated_hours: Decimal | None = None
    actual_hours: Decimal | None = None
    progress_percent: int | None = Field(default=None, ge=0, le=100)


class TaskRead(TaskBase):
    """Task response."""

    model_config = ConfigDict(from_attributes=True)

    task_id: UUID
    organization_id: UUID
    status: TaskStatus
    actual_start_date: date | None = None
    actual_end_date: date | None = None
    actual_hours: Decimal = Decimal("0.00")
    progress_percent: int = 0
    is_deleted: bool = False
    created_at: datetime
    updated_at: datetime | None = None


class TaskBrief(BaseModel):
    """Brief task summary for lists."""

    model_config = ConfigDict(from_attributes=True)

    task_id: UUID
    task_code: str
    task_name: str
    status: TaskStatus
    priority: TaskPriority
    due_date: date | None = None
    progress_percent: int = 0


class TaskWithDetails(TaskRead):
    """Task with related data."""

    model_config = ConfigDict(from_attributes=True)

    project_name: str | None = None
    assigned_to_name: str | None = None
    parent_task_name: str | None = None
    subtask_count: int = 0
    dependency_count: int = 0


class TaskListResponse(BaseModel):
    """Paginated task list response."""

    items: list[TaskRead]
    total: int
    offset: int
    limit: int


# =============================================================================
# Task Status Update Schemas
# =============================================================================


class TaskAssignRequest(BaseModel):
    """Request to assign a task."""

    assigned_to_id: UUID


class TaskProgressRequest(BaseModel):
    """Request to update task progress."""

    progress_percent: int = Field(ge=0, le=100)


class TaskStartResponse(BaseModel):
    """Response after starting a task."""

    task_id: UUID
    status: TaskStatus
    actual_start_date: date


class TaskCompleteResponse(BaseModel):
    """Response after completing a task."""

    task_id: UUID
    status: TaskStatus
    actual_end_date: date
    progress_percent: int = 100
