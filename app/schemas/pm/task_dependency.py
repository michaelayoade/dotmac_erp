"""
Task Dependency Pydantic Schemas.

Schemas for task dependency management.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.pm import DependencyType

# =============================================================================
# Task Dependency Schemas
# =============================================================================


class TaskDependencyBase(BaseModel):
    """Base dependency schema."""

    depends_on_task_id: UUID
    dependency_type: DependencyType = DependencyType.FINISH_TO_START
    lag_days: int = 0


class TaskDependencyCreate(TaskDependencyBase):
    """Create dependency request."""

    pass


class TaskDependencyRead(TaskDependencyBase):
    """Dependency response."""

    model_config = ConfigDict(from_attributes=True)

    dependency_id: UUID
    task_id: UUID
    created_at: datetime


class TaskDependencyWithDetails(TaskDependencyRead):
    """Dependency with task details."""

    model_config = ConfigDict(from_attributes=True)

    depends_on_task_code: str
    depends_on_task_name: str


class TaskDependencyListResponse(BaseModel):
    """List of task dependencies."""

    items: list[TaskDependencyWithDetails]
    total: int
