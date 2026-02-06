"""Unified workflow task model for cross-module task management."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class WorkflowTaskStatus(str, enum.Enum):
    """Status of a workflow task."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class WorkflowTaskPriority(str, enum.Enum):
    """Priority levels for workflow tasks."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


class WorkflowTask(Base):
    """Unified workflow task."""

    __tablename__ = "workflow_task"
    __table_args__ = (
        Index("idx_workflow_task_assignee", "organization_id", "assignee_employee_id"),
        Index("idx_workflow_task_status", "organization_id", "status"),
        Index("idx_workflow_task_module", "organization_id", "module"),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    module: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    action_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    assignee_employee_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[WorkflowTaskStatus] = mapped_column(
        Enum(WorkflowTaskStatus, name="workflow_task_status"),
        default=WorkflowTaskStatus.PENDING,
    )
    priority: Mapped[WorkflowTaskPriority] = mapped_column(
        Enum(WorkflowTaskPriority, name="workflow_task_priority"),
        default=WorkflowTaskPriority.MEDIUM,
    )
    due_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )
