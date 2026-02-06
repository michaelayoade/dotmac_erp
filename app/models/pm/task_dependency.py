"""
Task Dependency Model - PM Schema.

Represents dependencies between tasks for scheduling and Gantt charts.
"""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.pm.task import Task


class DependencyType(str, enum.Enum):
    """
    Types of task dependencies.

    - FINISH_TO_START: Task B starts after Task A finishes (most common)
    - START_TO_START: Task B starts when Task A starts
    - FINISH_TO_FINISH: Task B finishes when Task A finishes
    - START_TO_FINISH: Task B finishes when Task A starts (rare)
    """

    FINISH_TO_START = "FINISH_TO_START"
    START_TO_START = "START_TO_START"
    FINISH_TO_FINISH = "FINISH_TO_FINISH"
    START_TO_FINISH = "START_TO_FINISH"


class TaskDependency(Base):
    """
    Task dependency relationship.

    Represents that one task depends on another, with a specific
    dependency type and optional lag/lead time.
    """

    __tablename__ = "task_dependency"
    __table_args__ = (
        UniqueConstraint("task_id", "depends_on_task_id", name="uq_task_dependency"),
        CheckConstraint(
            "task_id != depends_on_task_id", name="chk_task_dependency_self"
        ),
        {"schema": "pm"},
    )

    dependency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # The dependent task (this task depends on another)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pm.task.task_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The predecessor task (the task we depend on)
    depends_on_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pm.task.task_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Type of dependency
    dependency_type: Mapped[DependencyType] = mapped_column(
        Enum(DependencyType, name="dependency_type", schema="pm"),
        nullable=False,
        default=DependencyType.FINISH_TO_START,
    )

    # Lag in days (can be negative for lead time)
    lag_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    task: Mapped["Task"] = relationship(
        "Task",
        foreign_keys=[task_id],
        back_populates="dependencies",
    )
    depends_on_task: Mapped["Task"] = relationship(
        "Task",
        foreign_keys=[depends_on_task_id],
        back_populates="dependents",
    )

    def __repr__(self) -> str:
        return f"<TaskDependency {self.task_id} -> {self.depends_on_task_id} ({self.dependency_type.value})>"
