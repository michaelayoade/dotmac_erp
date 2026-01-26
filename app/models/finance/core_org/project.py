"""
Project Model - Core Org.

Extended with Project Management fields for tasks, milestones, and resources.
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
from app.models.people.base import ERPNextSyncMixin

if TYPE_CHECKING:
    from app.models.support.ticket import Ticket
    from app.models.pm.task import Task
    from app.models.pm.milestone import Milestone
    from app.models.pm.resource_allocation import ResourceAllocation
    from app.models.pm.time_entry import TimeEntry
    from app.models.finance.ar.customer import Customer
    from app.models.pm.project_template import ProjectTemplate


class ProjectStatus(str, enum.Enum):
    PLANNING = "PLANNING"
    ACTIVE = "ACTIVE"
    ON_HOLD = "ON_HOLD"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class ProjectType(str, enum.Enum):
    """Type of project for billing/costing purposes."""

    INTERNAL = "INTERNAL"
    CLIENT = "CLIENT"
    FIXED_PRICE = "FIXED_PRICE"
    TIME_MATERIAL = "TIME_MATERIAL"
    FIBER_OPTICS_INSTALLATION = "FIBER_OPTICS_INSTALLATION"
    AIR_FIBER_INSTALLATION = "AIR_FIBER_INSTALLATION"
    CABLE_RERUN = "CABLE_RERUN"
    FIBER_OPTICS_RELOCATION = "FIBER_OPTICS_RELOCATION"
    AIR_FIBER_RELOCATION = "AIR_FIBER_RELOCATION"


class ProjectPriority(str, enum.Enum):
    """Project priority level."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Project(Base, ERPNextSyncMixin):
    """
    Project for tracking and cost allocation.
    Supports sync from ERPNext Project DocType.
    """

    __tablename__ = "project"
    __table_args__ = (
        UniqueConstraint("organization_id", "project_code", name="uq_project_code"),
        {"schema": "core_org"},
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
    )

    project_code: Mapped[str] = mapped_column(String(20), nullable=False)
    project_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    business_unit_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.business_unit.business_unit_id"),
        nullable=True,
    )
    segment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.reporting_segment.segment_id"),
        nullable=True,
    )
    project_manager_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Timeline
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # Budget
    budget_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    budget_currency_code: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)

    # Status
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus, name="project_status"),
        nullable=False,
        default=ProjectStatus.ACTIVE,
    )

    # Accounting
    is_capitalizable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Cost center for project expenses
    cost_center_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.cost_center.cost_center_id"),
        nullable=True,
    )

    # Customer relationship (for client projects)
    customer_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ar.customer.customer_id"),
        nullable=True,
        index=True,
        comment="Customer for client projects (synced from ERPNext)",
    )

    # PM Extension fields
    percent_complete: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0.00"),
    )
    estimated_cost: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    actual_cost: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    project_priority: Mapped[ProjectPriority] = mapped_column(
        Enum(ProjectPriority, name="project_priority", schema="pm"),
        nullable=False,
        default=ProjectPriority.MEDIUM,
    )
    project_type: Mapped[ProjectType] = mapped_column(
        Enum(ProjectType, name="project_type", schema="pm"),
        nullable=False,
        default=ProjectType.INTERNAL,
    )

    project_template_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pm.project_template.template_id"),
        nullable=True,
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

    # Relationships
    tickets: Mapped[list["Ticket"]] = relationship(
        "Ticket",
        back_populates="project",
    )
    customer: Mapped[Optional["Customer"]] = relationship(
        "Customer",
        foreign_keys=[customer_id],
    )

    # PM Relationships
    tasks: Mapped[List["Task"]] = relationship(
        "Task",
        foreign_keys="Task.project_id",
        back_populates="project",
    )
    milestones: Mapped[List["Milestone"]] = relationship(
        "Milestone",
        foreign_keys="Milestone.project_id",
        back_populates="project",
    )
    resource_allocations: Mapped[List["ResourceAllocation"]] = relationship(
        "ResourceAllocation",
        foreign_keys="ResourceAllocation.project_id",
        back_populates="project",
    )
    time_entries: Mapped[List["TimeEntry"]] = relationship(
        "TimeEntry",
        foreign_keys="TimeEntry.project_id",
        back_populates="project",
    )
    project_template: Mapped[Optional["ProjectTemplate"]] = relationship(
        "ProjectTemplate",
        foreign_keys=[project_template_id],
        lazy="joined",
    )

    @property
    def is_overdue(self) -> bool:
        """Check if project is past end date and not completed."""
        if not self.end_date:
            return False
        if self.status in (ProjectStatus.COMPLETED, ProjectStatus.CANCELLED):
            return False
        return date.today() > self.end_date

    @property
    def task_count(self) -> int:
        """Get total task count for this project."""
        return len(self.tasks) if self.tasks else 0

    @property
    def active_team_size(self) -> int:
        """Get count of active team members."""
        if not self.resource_allocations:
            return 0
        return sum(1 for r in self.resource_allocations if r.is_current)
