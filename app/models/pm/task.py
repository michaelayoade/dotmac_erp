"""
Task Model - PM Schema.

Core work item entity for project management.
"""
import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import (
    AuditMixin,
    ERPNextSyncMixin,
    SoftDeleteMixin,
)

if TYPE_CHECKING:
    from app.models.finance.core_org.project import Project
    from app.models.people.hr.employee import Employee
    from app.models.pm.task_dependency import TaskDependency
    from app.models.pm.milestone import Milestone
    from app.models.pm.time_entry import TimeEntry


class TaskStatus(str, enum.Enum):
    """Task lifecycle status."""

    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    PENDING_REVIEW = "PENDING_REVIEW"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    ON_HOLD = "ON_HOLD"


class TaskPriority(str, enum.Enum):
    """Task priority levels."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


class Task(Base, AuditMixin, SoftDeleteMixin, ERPNextSyncMixin):
    """
    Project task/work item with hierarchy and status tracking.

    Tasks support:
    - Hierarchical structure (parent-child relationships)
    - Dependencies between tasks
    - Assignment to employees
    - Progress tracking (0-100%)
    - Time estimation and tracking
    """

    __tablename__ = "task"
    __table_args__ = (
        UniqueConstraint("organization_id", "task_code", name="uq_task_org_code"),
        {"schema": "pm"},
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

    # Parent project (required)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.project.project_id"),
        nullable=False,
        index=True,
    )

    # Task hierarchy (optional parent)
    parent_task_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pm.task.task_id"),
        nullable=True,
        index=True,
    )

    # Basic fields
    task_code: Mapped[str] = mapped_column(String(30), nullable=False)
    task_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Status and priority
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="task_status", schema="pm"),
        nullable=False,
        default=TaskStatus.OPEN,
    )
    priority: Mapped[TaskPriority] = mapped_column(
        Enum(TaskPriority, name="task_priority", schema="pm"),
        nullable=False,
        default=TaskPriority.MEDIUM,
    )

    # Assignment
    assigned_to_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
        index=True,
    )

    # Planned dates
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)

    # Actual dates
    actual_start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    actual_end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Time tracking
    estimated_hours: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    actual_hours: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0.00")
    )

    # Progress
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Timestamps
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

    # Relationships
    project: Mapped["Project"] = relationship(
        "Project",
        foreign_keys=[project_id],
        lazy="joined",
    )
    parent_task: Mapped[Optional["Task"]] = relationship(
        "Task",
        remote_side=[task_id],
        foreign_keys=[parent_task_id],
        back_populates="subtasks",
    )
    subtasks: Mapped[List["Task"]] = relationship(
        "Task",
        back_populates="parent_task",
        foreign_keys=[parent_task_id],
    )
    assigned_to: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[assigned_to_id],
        lazy="joined",
    )

    # Dependencies (tasks this task depends on)
    dependencies: Mapped[List["TaskDependency"]] = relationship(
        "TaskDependency",
        foreign_keys="TaskDependency.task_id",
        back_populates="task",
        cascade="all, delete-orphan",
    )

    # Dependents (tasks that depend on this task)
    dependents: Mapped[List["TaskDependency"]] = relationship(
        "TaskDependency",
        foreign_keys="TaskDependency.depends_on_task_id",
        back_populates="depends_on_task",
    )

    # Time entries
    time_entries: Mapped[List["TimeEntry"]] = relationship(
        "TimeEntry",
        back_populates="task",
    )

    # Linked milestone (optional)
    milestones: Mapped[List["Milestone"]] = relationship(
        "Milestone",
        back_populates="linked_task",
    )

    def __repr__(self) -> str:
        return f"<Task {self.task_code}: {self.task_name}>"

    @property
    def is_overdue(self) -> bool:
        """Check if task is past due date and not completed."""
        if not self.due_date:
            return False
        if self.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
            return False
        return date.today() > self.due_date

    @property
    def is_started(self) -> bool:
        """Check if task has been started."""
        return self.status not in (TaskStatus.OPEN, TaskStatus.CANCELLED)

    @property
    def is_completed(self) -> bool:
        """Check if task is completed."""
        return self.status == TaskStatus.COMPLETED
