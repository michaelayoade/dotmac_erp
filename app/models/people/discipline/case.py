"""
Disciplinary Case Model - HR Schema.

The main entity for tracking employee policy violations and disciplinary proceedings.
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
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, SoftDeleteMixin, StatusTrackingMixin

if TYPE_CHECKING:
    from app.models.finance.core_org.organization import Organization
    from app.models.people.discipline.case_action import CaseAction
    from app.models.people.discipline.case_document import CaseDocument
    from app.models.people.discipline.case_response import CaseResponse
    from app.models.people.discipline.case_witness import CaseWitness
    from app.models.people.hr.employee import Employee


class ViolationType(str, enum.Enum):
    """Types of policy violations."""

    MISCONDUCT = "MISCONDUCT"  # General misconduct
    GROSS_MISCONDUCT = "GROSS_MISCONDUCT"  # Severe misconduct
    ATTENDANCE = "ATTENDANCE"  # Attendance issues
    PERFORMANCE = "PERFORMANCE"  # Performance-related
    INSUBORDINATION = "INSUBORDINATION"  # Refusal to follow instructions
    HARASSMENT = "HARASSMENT"  # Harassment complaints
    THEFT = "THEFT"  # Theft or fraud
    SAFETY_VIOLATION = "SAFETY_VIOLATION"  # Health & safety breaches
    POLICY_BREACH = "POLICY_BREACH"  # General policy violations
    CONFLICT_OF_INTEREST = "CONFLICT_OF_INTEREST"  # COI issues
    OTHER = "OTHER"  # Other violations


class SeverityLevel(str, enum.Enum):
    """Severity levels for violations."""

    MINOR = "MINOR"  # Minor infractions
    MODERATE = "MODERATE"  # Moderate issues
    MAJOR = "MAJOR"  # Major violations
    CRITICAL = "CRITICAL"  # Critical/gross misconduct


class CaseStatus(str, enum.Enum):
    """Disciplinary case workflow status."""

    DRAFT = "DRAFT"  # Initial case creation
    QUERY_ISSUED = "QUERY_ISSUED"  # Query sent to employee
    RESPONSE_RECEIVED = "RESPONSE_RECEIVED"  # Employee has responded
    UNDER_INVESTIGATION = "UNDER_INVESTIGATION"  # Being investigated
    HEARING_SCHEDULED = "HEARING_SCHEDULED"  # Hearing date set
    HEARING_COMPLETED = "HEARING_COMPLETED"  # Hearing held
    DECISION_MADE = "DECISION_MADE"  # Outcome determined
    APPEAL_FILED = "APPEAL_FILED"  # Employee appealed
    APPEAL_DECIDED = "APPEAL_DECIDED"  # Appeal outcome determined
    CLOSED = "CLOSED"  # Case closed
    WITHDRAWN = "WITHDRAWN"  # Case withdrawn


class DisciplinaryCase(Base, AuditMixin, SoftDeleteMixin, StatusTrackingMixin):
    """
    Disciplinary Case Model.

    Tracks employee policy violations from initial query through
    hearing, decision, and potential appeal.
    """

    __tablename__ = "disciplinary_case"
    __table_args__ = (
        Index("ix_discipline_case_org_status", "organization_id", "status"),
        Index("ix_discipline_case_employee", "employee_id"),
        {"schema": "hr"},
    )

    # Primary key
    case_id: Mapped[uuid.UUID] = mapped_column(
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
    )

    # Case identification
    case_number: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        unique=True,
        comment="Unique case reference number",
    )

    # Employee being disciplined
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )

    # Violation details
    violation_type: Mapped[ViolationType] = mapped_column(
        Enum(ViolationType, name="violation_type", schema="hr"),
        nullable=False,
    )
    severity: Mapped[SeverityLevel] = mapped_column(
        Enum(SeverityLevel, name="severity_level", schema="hr"),
        nullable=False,
        default=SeverityLevel.MODERATE,
    )
    subject: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Brief description of the violation",
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Detailed description of the incident",
    )

    # Key dates
    incident_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Date the incident occurred",
    )
    reported_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        default=date.today,
        comment="Date the incident was reported",
    )
    query_issued_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Date query was issued to employee",
    )
    response_due_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Deadline for employee response",
    )
    hearing_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Scheduled hearing date and time",
    )
    decision_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Date decision was made",
    )
    appeal_deadline: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Deadline to file appeal",
    )
    closed_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Date case was closed",
    )

    # Status
    status: Mapped[CaseStatus] = mapped_column(
        Enum(CaseStatus, name="case_status", schema="hr"),
        nullable=False,
        default=CaseStatus.DRAFT,
    )

    # Query details
    query_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Formal query text sent to employee",
    )

    # Hearing details
    hearing_location: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Location/room for hearing",
    )
    hearing_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Notes from the hearing",
    )

    # Decision details
    decision_summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Summary of the decision",
    )

    # Appeal details
    appeal_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Reason for appeal if filed",
    )
    appeal_decision: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Outcome of appeal",
    )

    # Reporting officer
    reported_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
        comment="Employee who reported the incident",
    )

    # Investigating officer
    investigating_officer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
        comment="HR officer handling the investigation",
    )

    # Hearing panel chair
    panel_chair_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
        comment="Chair of the disciplinary panel",
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
    employee: Mapped["Employee"] = relationship(
        "Employee",
        foreign_keys=[employee_id],
        back_populates="disciplinary_cases",
    )
    reported_by: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[reported_by_id],
    )
    investigating_officer: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[investigating_officer_id],
    )
    panel_chair: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[panel_chair_id],
    )
    organization: Mapped["Organization"] = relationship("Organization")
    witnesses: Mapped[list["CaseWitness"]] = relationship(
        "CaseWitness",
        back_populates="case",
        cascade="all, delete-orphan",
    )
    actions: Mapped[list["CaseAction"]] = relationship(
        "CaseAction",
        back_populates="case",
        cascade="all, delete-orphan",
    )
    documents: Mapped[list["CaseDocument"]] = relationship(
        "CaseDocument",
        back_populates="case",
        cascade="all, delete-orphan",
    )
    responses: Mapped[list["CaseResponse"]] = relationship(
        "CaseResponse",
        back_populates="case",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<DisciplinaryCase {self.case_number} - {self.status.value}>"
