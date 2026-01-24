"""
Employee lifecycle models - HR Schema.

Tracks onboarding, separation, promotions, and transfers.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Date, Enum, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, ERPNextSyncMixin


class BoardingStatus(str, enum.Enum):
    """Lifecycle status for onboarding/separation."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


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
    """Employee onboarding record."""

    __tablename__ = "employee_onboarding"
    __table_args__ = (
        Index("idx_onboarding_status", "organization_id", "status"),
        Index("idx_onboarding_employee", "organization_id", "employee_id"),
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
    job_applicant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recruit.job_applicant.applicant_id"),
        nullable=True,
    )
    job_offer_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recruit.job_offer.offer_id"),
        nullable=True,
    )
    date_of_joining: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
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
    template_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    status: Mapped[BoardingStatus] = mapped_column(
        Enum(BoardingStatus, name="boarding_status"),
        default=BoardingStatus.PENDING,
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(nullable=True, onupdate=func.now())

    activities: Mapped[list["EmployeeOnboardingActivity"]] = relationship(
        "EmployeeOnboardingActivity",
        back_populates="onboarding",
        cascade="all, delete-orphan",
    )


class EmployeeOnboardingActivity(Base):
    """Onboarding activity/task."""

    __tablename__ = "employee_onboarding_activity"
    __table_args__ = (
        Index("idx_onboarding_activity_onboarding", "onboarding_id"),
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
    assignee_role: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    completed_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0)

    onboarding: Mapped["EmployeeOnboarding"] = relationship(back_populates="activities")


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
    separation_type: Mapped[Optional[SeparationType]] = mapped_column(
        Enum(SeparationType, name="separation_type"),
        nullable=True,
    )
    resignation_letter_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    separation_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
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
    reason_for_leaving: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    exit_interview: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    template_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    status: Mapped[BoardingStatus] = mapped_column(
        Enum(BoardingStatus, name="separation_status"),
        default=BoardingStatus.PENDING,
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(nullable=True, onupdate=func.now())

    activities: Mapped[list["EmployeeSeparationActivity"]] = relationship(
        "EmployeeSeparationActivity",
        back_populates="separation",
        cascade="all, delete-orphan",
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
    assignee_role: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    completed_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0)

    separation: Mapped["EmployeeSeparation"] = relationship(back_populates="activities")


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
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(nullable=True, onupdate=func.now())

    details: Mapped[list["EmployeePromotionDetail"]] = relationship(
        "EmployeePromotionDetail",
        back_populates="promotion",
        cascade="all, delete-orphan",
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
    current_value: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0)

    promotion: Mapped["EmployeePromotion"] = relationship(back_populates="details")


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
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(nullable=True, onupdate=func.now())

    details: Mapped[list["EmployeeTransferDetail"]] = relationship(
        "EmployeeTransferDetail",
        back_populates="transfer",
        cascade="all, delete-orphan",
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
    current_value: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0)

    transfer: Mapped["EmployeeTransfer"] = relationship(back_populates="details")
