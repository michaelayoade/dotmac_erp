"""
Time Entry Model - PM Schema.

Time entries for project time tracking and billing.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    Text,
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
    from app.models.pm.task import Task


class BillingStatus(str, enum.Enum):
    """Billing status for time entries."""

    NOT_BILLED = "NOT_BILLED"
    BILLED = "BILLED"
    NON_BILLABLE = "NON_BILLABLE"


class TimeEntry(Base, AuditMixin):
    """
    Time entry for project time tracking.

    Records hours worked on a project/task by an employee with:
    - Billable/non-billable designation
    - Billing status tracking
    - ERPNext timesheet sync support
    """

    __tablename__ = "time_entry"
    __table_args__ = {"schema": "pm"}

    entry_id: Mapped[uuid.UUID] = mapped_column(
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

    # Project (required) and Task (optional)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.project.project_id"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pm.task.task_id"),
        nullable=True,
        index=True,
    )

    # Employee who logged the time
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
        index=True,
    )

    # Time entry details
    entry_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    hours: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Billing
    is_billable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    billing_status: Mapped[BillingStatus] = mapped_column(
        Enum(BillingStatus, name="billing_status", schema="pm"),
        nullable=False,
        default=BillingStatus.NOT_BILLED,
    )

    # ERPNext sync fields
    erpnext_timesheet_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    erpnext_timesheet_detail_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
    task: Mapped[Optional["Task"]] = relationship(
        "Task",
        foreign_keys=[task_id],
        back_populates="time_entries",
    )
    employee: Mapped["Employee"] = relationship(
        "Employee",
        foreign_keys=[employee_id],
        lazy="joined",
    )

    def __repr__(self) -> str:
        return f"<TimeEntry {self.entry_date}: {self.hours}h by {self.employee_id}>"

    def mark_billed(self) -> None:
        """Mark time entry as billed."""
        if self.is_billable:
            self.billing_status = BillingStatus.BILLED

    def mark_non_billable(self) -> None:
        """Mark time entry as non-billable."""
        self.is_billable = False
        self.billing_status = BillingStatus.NON_BILLABLE
