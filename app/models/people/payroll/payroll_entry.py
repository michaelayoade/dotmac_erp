"""
Payroll Entry Model - Payroll Schema.

Manages bulk payroll processing runs.
"""
import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    Date,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, ERPNextSyncMixin, StatusTrackingMixin
from app.models.people.payroll.salary_structure import PayrollFrequency

if TYPE_CHECKING:
    from app.models.people.hr.department import Department
    from app.models.people.payroll.salary_slip import SalarySlip


class PayrollEntryStatus(str, enum.Enum):
    """Payroll entry lifecycle status."""

    DRAFT = "DRAFT"
    SLIPS_CREATED = "SLIPS_CREATED"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    POSTED = "POSTED"
    CANCELLED = "CANCELLED"


class PayrollEntry(Base, AuditMixin, ERPNextSyncMixin, StatusTrackingMixin):
    """
    Payroll Entry - bulk payroll processing run.

    Creates salary slips for multiple employees in a pay period.
    Supports filtering by department, designation, etc.
    """

    __tablename__ = "payroll_entry"
    __table_args__ = (
        Index("idx_payroll_entry_period", "organization_id", "start_date", "end_date"),
        {"schema": "payroll"},
    )

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

    # Entry identification
    entry_number: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    # Period
    posting_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    start_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="Pay period start",
    )
    end_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="Pay period end",
    )
    payroll_frequency: Mapped[PayrollFrequency] = mapped_column(
        Enum(PayrollFrequency, name="payroll_frequency", create_type=False),
        default=PayrollFrequency.MONTHLY,
    )

    # Currency
    currency_code: Mapped[str] = mapped_column(
        String(3),
        default="NGN",
    )
    exchange_rate: Mapped[Decimal] = mapped_column(
        Numeric(18, 6),
        default=Decimal("1.0"),
    )

    # Filters
    department_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.department.department_id"),
        nullable=True,
        comment="Filter by department",
    )
    designation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.designation.designation_id"),
        nullable=True,
        comment="Filter by designation",
    )

    # Totals (aggregated from slips)
    total_gross_pay: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
    )
    total_deductions: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
    )
    total_net_pay: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
    )
    employee_count: Mapped[int] = mapped_column(
        default=0,
    )

    # Status
    status: Mapped[PayrollEntryStatus] = mapped_column(
        Enum(PayrollEntryStatus, name="payroll_entry_status"),
        default=PayrollEntryStatus.DRAFT,
    )
    salary_slips_created: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    salary_slips_submitted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
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
    department: Mapped[Optional["Department"]] = relationship(
        "Department",
        foreign_keys=[department_id],
    )
    salary_slips: Mapped[list["SalarySlip"]] = relationship(
        "SalarySlip",
        foreign_keys="SalarySlip.payroll_entry_id",
        back_populates="payroll_entry",
    )

    @property
    def status_label(self) -> str:
        """Human-readable status."""
        labels = {
            PayrollEntryStatus.DRAFT: "Draft",
            PayrollEntryStatus.SLIPS_CREATED: "Slips Created",
            PayrollEntryStatus.SUBMITTED: "Submitted",
            PayrollEntryStatus.APPROVED: "Approved",
            PayrollEntryStatus.POSTED: "Posted",
            PayrollEntryStatus.CANCELLED: "Cancelled",
        }
        return labels.get(self.status, "Draft")

    def __repr__(self) -> str:
        return f"<PayrollEntry {self.entry_number} ({self.start_date} - {self.end_date})>"
