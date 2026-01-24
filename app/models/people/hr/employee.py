"""
Employee Model - HR Schema.

The central entity linking Person to HR functionality.
"""
import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    Date,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import (
    AuditMixin,
    ERPNextSyncMixin,
    SoftDeleteMixin,
    VersionMixin,
)

if TYPE_CHECKING:
    from app.models.person import Person
    from app.models.finance.core_org.organization import Organization
    from app.models.finance.core_org.cost_center import CostCenter
    from app.models.finance.core_org.location import Location
    from app.models.finance.gl.account import Account
    from app.models.people.attendance.shift_type import ShiftType
    from app.models.people.hr.department import Department
    from app.models.people.hr.designation import Designation
    from app.models.people.hr.employment_type import EmploymentType
    from app.models.people.hr.employee_grade import EmployeeGrade
    from app.models.support.ticket import Ticket
    from app.models.support.team import SupportTeamMember


class EmployeeStatus(str, enum.Enum):
    """Employee lifecycle status."""

    DRAFT = "DRAFT"  # New hire, not yet active
    ACTIVE = "ACTIVE"  # Currently employed
    ON_LEAVE = "ON_LEAVE"  # Extended leave (sabbatical, etc.)
    SUSPENDED = "SUSPENDED"  # Disciplinary suspension
    RESIGNED = "RESIGNED"  # Voluntarily left
    TERMINATED = "TERMINATED"  # Involuntarily separated
    RETIRED = "RETIRED"  # Retired


class Gender(str, enum.Enum):
    """Gender options."""

    MALE = "MALE"
    FEMALE = "FEMALE"
    OTHER = "OTHER"
    PREFER_NOT_TO_SAY = "PREFER_NOT_TO_SAY"


class Employee(Base, AuditMixin, SoftDeleteMixin, ERPNextSyncMixin, VersionMixin):
    """
    Employee entity - links Person to HR functionality.

    This is the central model for all HR operations. Each Employee:
    - Must be linked to a Person (unified identity)
    - Belongs to exactly one Organization
    - Can have a Department, Designation, Grade, and Employment Type
    - Can report to another Employee (manager)
    - Can have a Cost Center for GL dimension posting
    """

    __tablename__ = "employee"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "employee_code", name="uq_employee_org_code"
        ),
        UniqueConstraint("person_id", name="uq_employee_person"),
        Index("idx_employee_org_dept", "organization_id", "department_id"),
        Index("idx_employee_org_status", "organization_id", "status"),
        {"schema": "hr"},
    )

    employee_id: Mapped[uuid.UUID] = mapped_column(
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

    # Link to unified identity
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Employee identification
    employee_code: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        comment="Unique employee code, e.g. EMP-2024-0001",
    )

    # Personal information (supplements Person)
    gender: Mapped[Optional[Gender]] = mapped_column(
        Enum(Gender, name="hr_gender"),
        nullable=True,
    )
    date_of_birth: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    personal_email: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Personal email (separate from work email on Person)",
    )
    personal_phone: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )
    emergency_contact_name: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    emergency_contact_phone: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )

    # Organization structure
    department_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.department.department_id"),
        nullable=True,
    )
    designation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.designation.designation_id"),
        nullable=True,
    )
    employment_type_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employment_type.employment_type_id"),
        nullable=True,
    )
    grade_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee_grade.grade_id"),
        nullable=True,
    )

    # Reporting hierarchy
    reports_to_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )

    # Attendance / geolocation defaults
    assigned_location_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.location.location_id"),
        nullable=True,
    )
    default_shift_type_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("attendance.shift_type.shift_type_id"),
        nullable=True,
    )

    # Employment dates
    date_of_joining: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    date_of_leaving: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    probation_end_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    confirmation_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )

    # Status
    status: Mapped[EmployeeStatus] = mapped_column(
        Enum(EmployeeStatus, name="employee_status"),
        nullable=False,
        default=EmployeeStatus.DRAFT,
    )

    # GL Integration
    cost_center_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.cost_center.cost_center_id"),
        nullable=True,
        comment="Default cost center for salary expenses",
    )
    default_payroll_payable_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=True,
        comment="Default payable account for net salary",
    )

    # Bank details (for payroll disbursement)
    bank_name: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    bank_account_number: Mapped[Optional[str]] = mapped_column(
        String(30),
        nullable=True,
    )
    bank_account_name: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    bank_branch_code: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
    )

    # Notes
    notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        foreign_keys=[organization_id],
    )
    person: Mapped["Person"] = relationship(
        "Person",
        foreign_keys=[person_id],
    )
    department: Mapped[Optional["Department"]] = relationship(
        "Department",
        foreign_keys=[department_id],
        back_populates="employees",
    )
    designation: Mapped[Optional["Designation"]] = relationship(
        "Designation",
        foreign_keys=[designation_id],
        back_populates="employees",
    )
    employment_type: Mapped[Optional["EmploymentType"]] = relationship(
        "EmploymentType",
        foreign_keys=[employment_type_id],
        back_populates="employees",
    )
    grade: Mapped[Optional["EmployeeGrade"]] = relationship(
        "EmployeeGrade",
        foreign_keys=[grade_id],
        back_populates="employees",
    )
    manager: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        remote_side=[employee_id],
        foreign_keys=[reports_to_id],
    )
    direct_reports: Mapped[list["Employee"]] = relationship(
        "Employee",
        back_populates="manager",
        foreign_keys=[reports_to_id],
    )
    cost_center: Mapped[Optional["CostCenter"]] = relationship(
        "CostCenter",
        foreign_keys=[cost_center_id],
    )
    payroll_payable_account: Mapped[Optional["Account"]] = relationship(
        "Account",
        foreign_keys=[default_payroll_payable_account_id],
    )
    assigned_location: Mapped[Optional["Location"]] = relationship(
        "Location",
        foreign_keys=[assigned_location_id],
    )
    default_shift_type: Mapped[Optional["ShiftType"]] = relationship(
        "ShiftType",
        foreign_keys=[default_shift_type_id],
    )

    # Ticket relationships (support module)
    raised_tickets: Mapped[list["Ticket"]] = relationship(
        "Ticket",
        foreign_keys="[Ticket.raised_by_id]",
        back_populates="raised_by",
    )
    assigned_tickets: Mapped[list["Ticket"]] = relationship(
        "Ticket",
        foreign_keys="[Ticket.assigned_to_id]",
        back_populates="assigned_to",
    )
    support_team_memberships: Mapped[list["SupportTeamMember"]] = relationship(
        "SupportTeamMember",
        back_populates="employee",
    )

    @property
    def full_name(self) -> str:
        """Get employee's full name from linked Person."""
        if not self.person:
            return self.employee_code
        return self.person.name

    @property
    def work_email(self) -> Optional[str]:
        """Get employee's work email from linked Person."""
        if self.person:
            return self.person.email
        return None
