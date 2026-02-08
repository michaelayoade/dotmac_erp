"""
Support Team Model.

Represents teams that handle support tickets.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

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
    from app.models.people.hr import Employee
    from app.models.support.ticket import Ticket


class SupportTeam(Base):
    """
    Support team for handling tickets.

    Teams can be assigned to tickets for routing and queue management.
    Each team has members and optionally a team lead.
    """

    __tablename__ = "support_team"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "team_code",
            name="uq_support_team_org_code",
        ),
        {"schema": "support"},
    )

    # Primary key
    team_id: Mapped[uuid.UUID] = mapped_column(
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

    # Team identification
    team_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Short code for team (e.g., FIBER, BILLING)",
    )
    team_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Display name for team",
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Team lead
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
        comment="Team lead/manager",
    )

    # Settings
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    auto_assign: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Auto-assign tickets to team members round-robin",
    )

    # For SLA (optional)
    default_response_hours: Mapped[int | None] = mapped_column(
        nullable=True,
        comment="Default SLA response time in hours",
    )
    default_resolution_hours: Mapped[int | None] = mapped_column(
        nullable=True,
        comment="Default SLA resolution time in hours",
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
        back_populates="support_teams",
    )
    lead: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[lead_id],
    )
    members: Mapped[list["SupportTeamMember"]] = relationship(
        "SupportTeamMember",
        back_populates="team",
        cascade="all, delete-orphan",
    )
    tickets: Mapped[list["Ticket"]] = relationship(
        "Ticket",
        back_populates="team",
    )

    def __repr__(self) -> str:
        return f"<SupportTeam(id={self.team_id}, code={self.team_code})>"


class SupportTeamMember(Base):
    """
    Member of a support team.

    Links employees to teams with optional roles.
    """

    __tablename__ = "support_team_member"
    __table_args__ = (
        UniqueConstraint(
            "team_id",
            "employee_id",
            name="uq_team_member",
        ),
        {"schema": "support"},
    )

    # Primary key
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # Foreign keys
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("support.support_team.team_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
        index=True,
    )

    # Role in team
    role: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Role in team (e.g., member, senior, specialist)",
    )

    # For round-robin assignment
    is_available: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="Available for auto-assignment",
    )
    assignment_weight: Mapped[int] = mapped_column(
        nullable=False,
        default=1,
        comment="Weight for assignment distribution (higher = more tickets)",
    )

    # Stats
    assigned_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        comment="Count of tickets assigned (for balancing)",
    )

    # Timestamps
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    team: Mapped["SupportTeam"] = relationship(
        "SupportTeam",
        back_populates="members",
    )
    employee: Mapped["Employee"] = relationship(
        "Employee",
        back_populates="support_team_memberships",
    )

    def __repr__(self) -> str:
        return f"<SupportTeamMember(team={self.team_id}, employee={self.employee_id})>"
