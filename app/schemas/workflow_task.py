"""
Workflow task schemas.
"""
from datetime import datetime
from typing import Optional
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
    description: Optional[str] = None
    action_url: Optional[str] = None
    assignee_employee_id: Optional[UUID] = None
    status: WorkflowTaskStatus
    priority: WorkflowTaskPriority
    due_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


class WorkflowTaskStatusUpdate(BaseModel):
    """Update workflow task status."""

    status: WorkflowTaskStatus


class WorkflowTaskSnoozeRequest(BaseModel):
    """Snooze workflow task."""

    days: int = 1
