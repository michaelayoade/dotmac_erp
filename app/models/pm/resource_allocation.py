"""
Resource Allocation Model - PM Schema.

Tracks team member assignment and utilization on projects.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin

if TYPE_CHECKING:
    from app.models.finance.core_org.project import Project
    from app.models.people.hr.employee import Employee


class ResourceAllocation(Base, AuditMixin):
    """
    Resource allocation to projects with utilization tracking.

    Represents the assignment of an employee to a project with:
    - Percentage of time allocated (0-100%)
    - Date range for the allocation
    - Cost and billing rates for time tracking
    """

    __tablename__ = "resource_allocation"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "employee_id", "start_date", name="uq_resource_allocation"
        ),
        {"schema": "pm"},
    )

    allocation_id: Mapped[uuid.UUID] = mapped_column(
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

    # Project and employee
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.project.project_id"),
        nullable=False,
        index=True,
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
        index=True,
    )

    # Role on this project
    role_on_project: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Allocation percentage (0-100)
    allocation_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)

    # Date range
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Active flag
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Rates for costing/billing
    cost_rate_per_hour: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    billing_rate_per_hour: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
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
    employee: Mapped["Employee"] = relationship(
        "Employee",
        foreign_keys=[employee_id],
        lazy="joined",
    )

    def __repr__(self) -> str:
        return f"<ResourceAllocation {self.employee_id} -> {self.project_id} ({self.allocation_percent}%)>"

    @property
    def is_current(self) -> bool:
        """Check if allocation is currently active."""
        if not self.is_active:
            return False
        today = date.today()
        if self.end_date and today > self.end_date:
            return False
        return today >= self.start_date

    def end_allocation(self, end_date_value: date | None = None) -> None:
        """End this allocation."""
        self.end_date = end_date_value or date.today()
        self.is_active = False
