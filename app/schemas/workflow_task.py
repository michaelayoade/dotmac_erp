"""
Workflow task schemas.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.workflow_task import WorkflowTaskPriority, WorkflowTaskStatus


class WorkflowTaskRead(BaseModel):
    """Workflow task response."""

    model_config = ConfigDict(from_attributes=True)

    task_id: UUID
    organization_id: UUID
    source_type: str
    source_id: UUID
    module: str
    title: str
    description: str | None = None
    action_url: str | None = None
    assignee_employee_id: UUID | None = None
    status: WorkflowTaskStatus
    priority: WorkflowTaskPriority
    due_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


class WorkflowTaskStatusUpdate(BaseModel):
    """Update workflow task status."""

    status: WorkflowTaskStatus


class WorkflowTaskSnoozeRequest(BaseModel):
    """Snooze workflow task."""

    days: int = 1
