"""
Task Pydantic Schemas.

Schemas for PM Task API endpoints.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
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
    description: Optional[str] = None
    project_id: UUID
    parent_task_id: Optional[UUID] = None
    priority: TaskPriority = TaskPriority.MEDIUM
    assigned_to_id: Optional[UUID] = None
    start_date: Optional[date] = None
    due_date: Optional[date] = None
    estimated_hours: Optional[Decimal] = None


class TaskCreate(TaskBase):
    """Create task request."""

    pass


class TaskUpdate(BaseModel):
    """Update task request."""

    task_code: Optional[str] = Field(default=None, max_length=30)
    task_name: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = None
    parent_task_id: Optional[UUID] = None
    priority: Optional[TaskPriority] = None
    status: Optional[TaskStatus] = None
    assigned_to_id: Optional[UUID] = None
    start_date: Optional[date] = None
    due_date: Optional[date] = None
    estimated_hours: Optional[Decimal] = None
    actual_hours: Optional[Decimal] = None
    progress_percent: Optional[int] = Field(default=None, ge=0, le=100)


class TaskRead(TaskBase):
    """Task response."""

    model_config = ConfigDict(from_attributes=True)

    task_id: UUID
    organization_id: UUID
    status: TaskStatus
    actual_start_date: Optional[date] = None
    actual_end_date: Optional[date] = None
    actual_hours: Decimal = Decimal("0.00")
    progress_percent: int = 0
    is_deleted: bool = False
    created_at: datetime
    updated_at: Optional[datetime] = None


class TaskBrief(BaseModel):
    """Brief task summary for lists."""

    model_config = ConfigDict(from_attributes=True)

    task_id: UUID
    task_code: str
    task_name: str
    status: TaskStatus
    priority: TaskPriority
    due_date: Optional[date] = None
    progress_percent: int = 0


class TaskWithDetails(TaskRead):
    """Task with related data."""

    model_config = ConfigDict(from_attributes=True)

    project_name: Optional[str] = None
    assigned_to_name: Optional[str] = None
    parent_task_name: Optional[str] = None
    subtask_count: int = 0
    dependency_count: int = 0


class TaskListResponse(BaseModel):
    """Paginated task list response."""

    items: List[TaskRead]
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
