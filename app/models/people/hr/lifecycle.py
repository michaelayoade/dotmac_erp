"""
Employee lifecycle models - HR Schema.

Tracks onboarding, separation, promotions, and transfers.

Supports:
- Self-service onboarding portal with token-based access
- Task assignments with due dates and document collection
- Progress tracking and automated reminders
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, ERPNextSyncMixin

if TYPE_CHECKING:
    from app.models.finance.core_org import Organization
    from app.models.people.hr.checklist_template import (
        ChecklistTemplate,
        ChecklistTemplateItem,
    )
    from app.models.people.hr.employee import Employee


class BoardingStatus(str, enum.Enum):
    """Lifecycle status for onboarding/separation."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class ActivityStatus(str, enum.Enum):
    """Status for onboarding/separation activities."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    AWAITING_DOCUMENT = "AWAITING_DOCUMENT"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"


class SeparationType(str, enum.Enum):
    """Type of employee separation."""

    RESIGNATION = "RESIGNATION"
    TERMINATION = "TERMINATION"
    RETIREMENT = "RETIREMENT"
    REDUNDANCY = "REDUNDANCY"
    CONTRACT_END = "CONTRACT_END"
    DEATH = "DEATH"
    OTHER = "OTHER"


class EmployeeOnboarding(Base, AuditMixin, ERPNextSyncMixin):
    """
    Employee onboarding record.

    Tracks the onboarding process for a new employee, including:
    - Checklist activities from a template
    - Self-service portal access
    - Progress tracking
    - Assigned buddy/mentor
    """

    __tablename__ = "employee_onboarding"
    __table_args__ = (
        Index("idx_onboarding_status", "organization_id", "status"),
        Index("idx_onboarding_employee", "organization_id", "employee_id"),
        Index("idx_onboarding_self_service_token", "self_service_token", unique=True),
        {"schema": "hr"},
    )

    onboarding_id: Mapped[uuid.UUID] = mapped_column(
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
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )
    job_applicant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recruit.job_applicant.applicant_id"),
        nullable=True,
    )
    job_offer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recruit.job_offer.offer_id"),
        nullable=True,
    )
    date_of_joining: Mapped[date | None] = mapped_column(Date, nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.department.department_id"),
        nullable=True,
    )
    designation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.designation.designation_id"),
        nullable=True,
    )
    template_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[BoardingStatus] = mapped_column(
        Enum(BoardingStatus, name="boarding_status"),
        default=BoardingStatus.PENDING,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        nullable=True, onupdate=func.now()
    )

    # --- New fields for enhanced onboarding ---

    # Link to checklist template used
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.checklist_template.template_id"),
        nullable=True,
        comment="Checklist template used for this onboarding",
    )

    # Self-service portal access
    self_service_token: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Token for new hire self-service portal access",
    )
    self_service_token_expires: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Token expiry timestamp",
    )
    self_service_email_sent: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Whether welcome email with portal link has been sent",
    )

    # Completion tracking
    expected_completion_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Target date for completing all onboarding tasks",
    )
    actual_completion_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Date when onboarding was marked complete",
    )
    progress_percentage: Mapped[int] = mapped_column(
        Integer,
        default=0,
        comment="Calculated progress (0-100)",
    )

    # Buddy/mentor assignment
    buddy_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
        comment="Assigned buddy/mentor for the new employee",
    )

    # Manager for approvals
    manager_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
        comment="Direct manager for approvals and notifications",
    )

    # Relationships
    activities: Mapped[list[EmployeeOnboardingActivity]] = relationship(
        "EmployeeOnboardingActivity",
        back_populates="onboarding",
        cascade="all, delete-orphan",
    )
    template: Mapped[ChecklistTemplate | None] = relationship(
        "ChecklistTemplate",
        foreign_keys=[template_id],
    )
    employee: Mapped[Employee | None] = relationship(
        "Employee",
        foreign_keys=[employee_id],
    )
    organization: Mapped[Organization | None] = relationship(
        "Organization",
        foreign_keys=[organization_id],
    )


class EmployeeOnboardingActivity(Base):
    """
    Onboarding activity/task.

    Represents a specific task in an employee's onboarding checklist.
    Can be assigned to HR, manager, IT, or the employee themselves (self-service).
    """

    __tablename__ = "employee_onboarding_activity"
    __table_args__ = (
        Index("idx_onboarding_activity_onboarding", "onboarding_id"),
        Index("idx_onboarding_activity_assignee", "assignee_id"),
        Index("idx_onboarding_activity_due_date", "due_date", "activity_status"),
        {"schema": "hr"},
    )

    activity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    onboarding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee_onboarding.onboarding_id", ondelete="CASCADE"),
        nullable=False,
    )
    activity_name: Mapped[str] = mapped_column(String(500), nullable=False)
    assignee_role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    completed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0)

    # --- New fields for enhanced onboarding ---

    # Link to template item (for tracking which template item created this)
    template_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.checklist_template_item.item_id"),
        nullable=True,
        comment="Template item this activity was created from",
    )

    # Task category/phase
    category: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
        comment="Task category: PRE_BOARDING, DAY_ONE, FIRST_WEEK, FIRST_MONTH, ONGOING",
    )

    # Due date and status tracking
    due_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Task deadline",
    )
    activity_status: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
        comment="Activity status: PENDING, IN_PROGRESS, AWAITING_DOCUMENT, COMPLETED, SKIPPED, BLOCKED",
    )
    is_overdue: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Whether task is past due date",
    )

    # Assignment
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
        comment="Specific person assigned to this task",
    )
    assigned_to_employee: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="True if this is a self-service task for the new employee",
    )

    # Document collection
    requires_document: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Whether document upload is required",
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="FK to uploaded document (if requires_document)",
    )

    # Completion tracking
    completed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
        comment="Person who completed this task",
    )
    completion_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Notes added when completing the task",
    )

    # Reminder tracking
    reminder_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of last reminder sent",
    )

    # Relationships
    onboarding: Mapped[EmployeeOnboarding] = relationship(back_populates="activities")
    template_item: Mapped[ChecklistTemplateItem | None] = relationship(
        "ChecklistTemplateItem",
        foreign_keys=[template_item_id],
    )


class EmployeeSeparation(Base, AuditMixin, ERPNextSyncMixin):
    """Employee separation record."""

    __tablename__ = "employee_separation"
    __table_args__ = (
        Index("idx_separation_status", "organization_id", "status"),
        Index("idx_separation_employee", "organization_id", "employee_id"),
        {"schema": "hr"},
    )

    separation_id: Mapped[uuid.UUID] = mapped_column(
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
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )
    separation_type: Mapped[SeparationType | None] = mapped_column(
        Enum(SeparationType, name="separation_type"),
        nullable=True,
    )
    resignation_letter_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    separation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.department.department_id"),
        nullable=True,
    )
    designation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.designation.designation_id"),
        nullable=True,
    )
    reason_for_leaving: Mapped[str | None] = mapped_column(Text, nullable=True)
    exit_interview: Mapped[str | None] = mapped_column(Text, nullable=True)
    template_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[BoardingStatus] = mapped_column(
        Enum(BoardingStatus, name="separation_status"),
        default=BoardingStatus.PENDING,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        nullable=True, onupdate=func.now()
    )

    activities: Mapped[list[EmployeeSeparationActivity]] = relationship(
        "EmployeeSeparationActivity",
        back_populates="separation",
        cascade="all, delete-orphan",
    )
    employee: Mapped[Employee | None] = relationship(
        "Employee",
        foreign_keys=[employee_id],
    )


class EmployeeSeparationActivity(Base):
    """Separation activity/task."""

    __tablename__ = "employee_separation_activity"
    __table_args__ = (
        Index("idx_separation_activity_separation", "separation_id"),
        {"schema": "hr"},
    )

    activity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    separation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee_separation.separation_id", ondelete="CASCADE"),
        nullable=False,
    )
    activity_name: Mapped[str] = mapped_column(String(500), nullable=False)
    assignee_role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    completed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0)

    separation: Mapped[EmployeeSeparation] = relationship(back_populates="activities")


class EmployeePromotion(Base, AuditMixin, ERPNextSyncMixin):
    """Employee promotion record."""

    __tablename__ = "employee_promotion"
    __table_args__ = (
        Index("idx_promotion_employee", "organization_id", "employee_id"),
        {"schema": "hr"},
    )

    promotion_id: Mapped[uuid.UUID] = mapped_column(
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
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )
    promotion_date: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        nullable=True, onupdate=func.now()
    )

    details: Mapped[list[EmployeePromotionDetail]] = relationship(
        "EmployeePromotionDetail",
        back_populates="promotion",
        cascade="all, delete-orphan",
    )
    employee: Mapped[Employee | None] = relationship(
        "Employee",
        foreign_keys=[employee_id],
    )


class EmployeePromotionDetail(Base):
    """Promotion detail record."""

    __tablename__ = "employee_promotion_detail"
    __table_args__ = (
        Index("idx_promotion_detail_promotion", "promotion_id"),
        {"schema": "hr"},
    )

    detail_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    promotion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee_promotion.promotion_id", ondelete="CASCADE"),
        nullable=False,
    )
    property_name: Mapped[str] = mapped_column(String(100), nullable=False)
    current_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    new_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0)

    promotion: Mapped[EmployeePromotion] = relationship(back_populates="details")


class EmployeeTransfer(Base, AuditMixin, ERPNextSyncMixin):
    """Employee transfer record."""

    __tablename__ = "employee_transfer"
    __table_args__ = (
        Index("idx_transfer_employee", "organization_id", "employee_id"),
        {"schema": "hr"},
    )

    transfer_id: Mapped[uuid.UUID] = mapped_column(
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
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )
    transfer_date: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        nullable=True, onupdate=func.now()
    )

    details: Mapped[list[EmployeeTransferDetail]] = relationship(
        "EmployeeTransferDetail",
        back_populates="transfer",
        cascade="all, delete-orphan",
    )
    employee: Mapped[Employee | None] = relationship(
        "Employee",
        foreign_keys=[employee_id],
    )


class EmployeeTransferDetail(Base):
    """Transfer detail record."""

    __tablename__ = "employee_transfer_detail"
    __table_args__ = (
        Index("idx_transfer_detail_transfer", "transfer_id"),
        {"schema": "hr"},
    )

    detail_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    transfer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee_transfer.transfer_id", ondelete="CASCADE"),
        nullable=False,
    )
    property_name: Mapped[str] = mapped_column(String(100), nullable=False)
    current_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    new_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0)

    transfer: Mapped[EmployeeTransfer] = relationship(back_populates="details")
