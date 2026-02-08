"""
Support Ticket Model.

Represents helpdesk tickets synced from ERPNext Issue or HD Ticket DocTypes.
Used for linking expense claims and other entities to support tickets.
"""

import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
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
    from app.models.finance.ar.customer import Customer
    from app.models.finance.core_org import Organization, Project
    from app.models.people.hr import Employee
    from app.models.support.attachment import TicketAttachment
    from app.models.support.category import TicketCategory
    from app.models.support.comment import TicketComment
    from app.models.support.team import SupportTeam


class TicketStatus(str, enum.Enum):
    """Ticket status following ERPNext Issue status workflow."""

    OPEN = "OPEN"
    REPLIED = "REPLIED"
    ON_HOLD = "ON_HOLD"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class TicketPriority(str, enum.Enum):
    """Ticket priority levels."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


# Mapping from ERPNext status to DotMac status
ERPNEXT_STATUS_MAP = {
    "Open": TicketStatus.OPEN,
    "Replied": TicketStatus.REPLIED,
    "On Hold": TicketStatus.ON_HOLD,
    "Hold": TicketStatus.ON_HOLD,
    "Resolved": TicketStatus.RESOLVED,
    "Closed": TicketStatus.CLOSED,
}

# Mapping from ERPNext priority to DotMac priority
ERPNEXT_PRIORITY_MAP = {
    "Low": TicketPriority.LOW,
    "Medium": TicketPriority.MEDIUM,
    "High": TicketPriority.HIGH,
    "Urgent": TicketPriority.URGENT,
}


class Ticket(Base, AuditMixin, ERPNextSyncMixin):
    """
    Support ticket synced from ERPNext.

    Can be linked to:
    - Expense claims (for support-related expenses)
    - Projects (inherited from ERPNext Issue)
    - Employees (raised_by, assigned_to)
    """

    __tablename__ = "ticket"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "ticket_number",
            name="uq_ticket_org_number",
        ),
        {"schema": "support"},
    )

    # Primary key
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # Organization (multi-tenancy)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )

    # Ticket identification
    ticket_number: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
        comment="Unique ticket number (ERPNext Issue name)",
    )
    subject: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Status and priority
    status: Mapped[TicketStatus] = mapped_column(
        Enum(TicketStatus, name="ticket_status", schema="support"),
        nullable=False,
        default=TicketStatus.OPEN,
        index=True,
    )
    priority: Mapped[TicketPriority] = mapped_column(
        Enum(TicketPriority, name="ticket_priority", schema="support"),
        nullable=False,
        default=TicketPriority.MEDIUM,
    )

    # People assignments (optional - may not have employees synced yet)
    raised_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
        comment="Employee who raised the ticket",
    )
    raised_by_email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Email of person who raised ticket (for lookup before employee sync)",
    )
    assigned_to_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
        comment="Employee assigned to handle the ticket",
    )

    # Project linkage (optional)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.project.project_id"),
        nullable=True,
        comment="Related project from ERPNext",
    )

    # Customer linkage (optional - for customer support tickets)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ar.customer.customer_id"),
        nullable=True,
        index=True,
        comment="Customer linked to this ticket (synced from ERPNext)",
    )

    # Contact info (can be auto-populated from customer or manually entered)
    contact_email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Contact email for this ticket (may differ from customer record)",
    )
    contact_phone: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Contact phone for this ticket",
    )
    contact_address: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Contact address for this ticket",
    )

    # Category (optional)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("support.ticket_category.category_id"),
        nullable=True,
        comment="Ticket category/type",
    )

    # Team assignment (optional)
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("support.support_team.team_id"),
        nullable=True,
        comment="Support team assigned to this ticket",
    )

    # Soft delete
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )

    # Resolution details
    resolution: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Resolution details",
    )

    # Dates
    opening_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        default=date.today,
    )
    resolution_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
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
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="tickets",
        lazy="selectin",
    )
    raised_by: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[raised_by_id],
        back_populates="raised_tickets",
    )
    assigned_to: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[assigned_to_id],
        back_populates="assigned_tickets",
    )
    project: Mapped[Optional["Project"]] = relationship(
        "Project",
        back_populates="tickets",
    )
    customer: Mapped[Optional["Customer"]] = relationship(
        "Customer",
        foreign_keys=[customer_id],
    )
    category: Mapped[Optional["TicketCategory"]] = relationship(
        "TicketCategory",
        back_populates="tickets",
    )
    team: Mapped[Optional["SupportTeam"]] = relationship(
        "SupportTeam",
        back_populates="tickets",
    )
    comments: Mapped[list["TicketComment"]] = relationship(
        "TicketComment",
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="TicketComment.created_at",
    )
    attachments: Mapped[list["TicketAttachment"]] = relationship(
        "TicketAttachment",
        back_populates="ticket",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Ticket(ticket_id={self.ticket_id}, number={self.ticket_number}, status={self.status})>"

    @classmethod
    def map_erpnext_status(cls, status: str) -> TicketStatus:
        """Map ERPNext Issue status to DotMac TicketStatus."""
        return ERPNEXT_STATUS_MAP.get(status, TicketStatus.OPEN)

    @classmethod
    def map_erpnext_priority(cls, priority: str) -> TicketPriority:
        """Map ERPNext Issue priority to DotMac TicketPriority."""
        return ERPNEXT_PRIORITY_MAP.get(priority, TicketPriority.MEDIUM)
