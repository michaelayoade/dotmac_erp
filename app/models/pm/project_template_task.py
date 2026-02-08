"""
Project Template Task Models - PM Schema.

Stores ordered tasks and dependencies for project templates.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.pm.task_dependency import DependencyType

if TYPE_CHECKING:
    from app.models.pm.project_template import ProjectTemplate


class ProjectTemplateTask(Base):
    """Task definition within a project template."""

    __tablename__ = "project_template_task"
    __table_args__ = (
        UniqueConstraint(
            "template_id", "order_index", name="uq_project_template_task_order"
        ),
        {"schema": "pm"},
    )

    template_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pm.project_template.template_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    task_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    template: Mapped["ProjectTemplate"] = relationship(
        "ProjectTemplate",
        back_populates="tasks",
    )

    dependencies: Mapped[list["ProjectTemplateTaskDependency"]] = relationship(
        "ProjectTemplateTaskDependency",
        foreign_keys="ProjectTemplateTaskDependency.template_task_id",
        back_populates="task",
        cascade="all, delete-orphan",
    )

    dependents: Mapped[list["ProjectTemplateTaskDependency"]] = relationship(
        "ProjectTemplateTaskDependency",
        foreign_keys="ProjectTemplateTaskDependency.depends_on_template_task_id",
        back_populates="depends_on_task",
    )

    def __repr__(self) -> str:
        return f"<ProjectTemplateTask {self.task_name}>"


class ProjectTemplateTaskDependency(Base):
    """Dependency relationship between template tasks."""

    __tablename__ = "project_template_task_dependency"
    __table_args__ = (
        UniqueConstraint(
            "template_task_id",
            "depends_on_template_task_id",
            name="uq_project_template_task_dependency",
        ),
        CheckConstraint(
            "template_task_id != depends_on_template_task_id",
            name="chk_project_template_task_dependency_self",
        ),
        {"schema": "pm"},
    )

    dependency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    template_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pm.project_template_task.template_task_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    depends_on_template_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pm.project_template_task.template_task_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    dependency_type: Mapped[DependencyType] = mapped_column(
        Enum(DependencyType, name="dependency_type", schema="pm"),
        nullable=False,
        default=DependencyType.FINISH_TO_START,
    )
    lag_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    task: Mapped["ProjectTemplateTask"] = relationship(
        "ProjectTemplateTask",
        foreign_keys=[template_task_id],
        back_populates="dependencies",
    )
    depends_on_task: Mapped["ProjectTemplateTask"] = relationship(
        "ProjectTemplateTask",
        foreign_keys=[depends_on_template_task_id],
        back_populates="dependents",
    )

    def __repr__(self) -> str:
        return (
            f"<ProjectTemplateTaskDependency {self.template_task_id} -> "
            f"{self.depends_on_template_task_id} ({self.dependency_type.value})>"
        )
