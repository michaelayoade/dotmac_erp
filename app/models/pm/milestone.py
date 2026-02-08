"""
Milestone Model - PM Schema.

Project milestones/phases with target and actual dates.
"""

import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, ERPNextSyncMixin

if TYPE_CHECKING:
    from app.models.finance.core_org.project import Project
    from app.models.pm.task import Task


class MilestoneStatus(str, enum.Enum):
    """Milestone achievement status."""

    PENDING = "PENDING"
    ACHIEVED = "ACHIEVED"
    MISSED = "MISSED"
    CANCELLED = "CANCELLED"


class Milestone(Base, AuditMixin, ERPNextSyncMixin):
    """
    Project milestone representing a significant point or phase.

    Milestones can optionally be linked to a specific task that
    represents the completion criteria for the milestone.
    """

    __tablename__ = "milestone"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "milestone_code", name="uq_milestone_org_code"
        ),
        {"schema": "pm"},
    )

    milestone_id: Mapped[uuid.UUID] = mapped_column(
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

    # Parent project
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.project.project_id"),
        nullable=False,
        index=True,
    )

    # Basic fields
    milestone_code: Mapped[str] = mapped_column(String(30), nullable=False)
    milestone_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Dates
    target_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    actual_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Status
    status: Mapped[MilestoneStatus] = mapped_column(
        Enum(MilestoneStatus, name="milestone_status", schema="pm"),
        nullable=False,
        default=MilestoneStatus.PENDING,
    )

    # Optional linked task
    linked_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pm.task.task_id"),
        nullable=True,
        index=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
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
    linked_task: Mapped[Optional["Task"]] = relationship(
        "Task",
        foreign_keys=[linked_task_id],
        back_populates="milestones",
    )

    def __repr__(self) -> str:
        return f"<Milestone {self.milestone_code}: {self.milestone_name}>"

    @property
    def is_overdue(self) -> bool:
        """Check if milestone is past target date and not achieved."""
        if self.status in (MilestoneStatus.ACHIEVED, MilestoneStatus.CANCELLED):
            return False
        return date.today() > self.target_date

    @property
    def days_until_target(self) -> int:
        """Days until target date (negative if overdue)."""
        return (self.target_date - date.today()).days

    def achieve(self, actual_date: date | None = None) -> None:
        """Mark milestone as achieved."""
        self.status = MilestoneStatus.ACHIEVED
        self.actual_date = actual_date or date.today()

    def mark_missed(self) -> None:
        """Mark milestone as missed."""
        self.status = MilestoneStatus.MISSED
