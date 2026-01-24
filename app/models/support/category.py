"""
Ticket Category Model.

Represents categories/types for support tickets.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
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

if TYPE_CHECKING:
    from app.models.finance.core_org import Organization
    from app.models.support.ticket import Ticket
    from app.models.support.team import SupportTeam


class TicketCategory(Base):
    """
    Category for classifying tickets.

    Examples: Network Issue, Billing, Hardware, Software, Feature Request
    Categories can have default team assignments and SLA settings.
    """

    __tablename__ = "ticket_category"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "category_code",
            name="uq_ticket_category_org_code",
        ),
        {"schema": "support"},
    )

    # Primary key
    category_id: Mapped[uuid.UUID] = mapped_column(
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

    # Category identification
    category_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Short code (e.g., NET, BILL, HW)",
    )
    category_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Display name",
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Display
    color: Mapped[Optional[str]] = mapped_column(
        String(7),
        nullable=True,
        comment="Hex color for badges (e.g., #FF5733)",
    )
    icon: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="Icon name for UI",
    )
    display_order: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        comment="Order in dropdowns/lists",
    )

    # Default assignment
    default_team_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("support.support_team.team_id"),
        nullable=True,
        comment="Default team for tickets in this category",
    )

    # Default SLA (optional)
    default_priority: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="Default priority for new tickets",
    )
    response_hours: Mapped[Optional[int]] = mapped_column(
        nullable=True,
        comment="SLA response time in hours",
    )
    resolution_hours: Mapped[Optional[int]] = mapped_column(
        nullable=True,
        comment="SLA resolution time in hours",
    )

    # Settings
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    requires_project: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Tickets in this category require project link",
    )

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
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="ticket_categories",
    )
    default_team: Mapped[Optional["SupportTeam"]] = relationship(
        "SupportTeam",
        foreign_keys=[default_team_id],
    )
    tickets: Mapped[List["Ticket"]] = relationship(
        "Ticket",
        back_populates="category",
    )

    def __repr__(self) -> str:
        return f"<TicketCategory(id={self.category_id}, code={self.category_code})>"
