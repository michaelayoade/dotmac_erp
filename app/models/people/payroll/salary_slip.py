"""
Salary Slip Model - Payroll Schema.

The core payroll document that generates GL entries.
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
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.people.base import AuditMixin, ERPNextSyncMixin, StatusTrackingMixin

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee
    from app.models.people.payroll.salary_structure import SalaryStructure
    from app.models.people.payroll.salary_component import SalaryComponent
    from app.models.people.payroll.payroll_entry import PayrollEntry
    from app.models.finance.gl.journal_entry import JournalEntry
    from app.models.finance.core_org.cost_center import CostCenter


class SalarySlipStatus(str, enum.Enum):
    """Salary slip lifecycle status."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    POSTED = "POSTED"
    PAID = "PAID"
    CANCELLED = "CANCELLED"


class SalarySlip(Base, AuditMixin, ERPNextSyncMixin, StatusTrackingMixin):
    """
    Salary Slip - employee monthly payslip.

    This is the core payroll document. When approved, it posts to GL:
    - Debit: Salary expense accounts (from earning components)
    - Credit: Payroll payable account (net pay)
    - Credit: Liability accounts (deduction components - PAYE, Pension, etc.)
    """

    __tablename__ = "salary_slip"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "employee_id",
            "start_date",
            "end_date",
            name="uq_salary_slip_emp_period",
        ),
        UniqueConstraint(
            "organization_id",
            "slip_number",
            name="uq_salary_slip_org_number",
        ),
        Index("idx_salary_slip_emp", "employee_id"),
        Index("idx_salary_slip_period", "organization_id", "start_date", "end_date"),
        Index("idx_salary_slip_status", "organization_id", "status"),
        Index("idx_salary_slip_needs_review", "organization_id", "needs_review"),
        {"schema": "payroll"},
    )

    slip_id: Mapped[uuid.UUID] = mapped_column(
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

    # Slip identification
    slip_number: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        comment="Auto-generated slip number",
    )

    # Employee reference
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )
    employee_name: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
        comment="Denormalized for reporting",
    )

    # Structure reference
    structure_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payroll.salary_structure.structure_id"),
        nullable=True,
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

    # Currency
    currency_code: Mapped[str] = mapped_column(
        String(3),
        default="NGN",
    )
    exchange_rate: Mapped[Decimal] = mapped_column(
        Numeric(18, 6),
        default=Decimal("1.0"),
    )

    # Payment days calculation
    total_working_days: Mapped[Decimal] = mapped_column(
        Numeric(8, 2),
        default=Decimal("0"),
    )
    absent_days: Mapped[Decimal] = mapped_column(
        Numeric(8, 2),
        default=Decimal("0"),
    )
    payment_days: Mapped[Decimal] = mapped_column(
        Numeric(8, 2),
        default=Decimal("0"),
    )
    leave_without_pay: Mapped[Decimal] = mapped_column(
        Numeric(8, 2),
        default=Decimal("0"),
    )

    # Amounts (in document currency)
    gross_pay: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
        comment="Total earnings",
    )
    total_deduction: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
        comment="Total deductions",
    )
    net_pay: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
        comment="Gross - Deductions",
    )

    # Functional currency amounts
    gross_pay_functional: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
    )
    total_deduction_functional: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
    )
    net_pay_functional: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
    )

    # GL Dimension (for cost allocation)
    cost_center_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.cost_center.cost_center_id"),
        nullable=True,
        comment="From employee's cost center",
    )

    # Status
    status: Mapped[SalarySlipStatus] = mapped_column(
        Enum(SalarySlipStatus, name="salary_slip_status"),
        default=SalarySlipStatus.DRAFT,
    )

    # GL Integration
    journal_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.journal_entry.journal_entry_id"),
        nullable=True,
        comment="Posted GL entry",
    )
    posted_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
    )
    posted_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
    )

    # Payment tracking
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
    )
    paid_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
    )
    payment_reference: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )

    # Bank details (denormalized from employee)
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

    # Payroll entry link (for bulk processing)
    payroll_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payroll.payroll_entry.entry_id"),
        nullable=True,
    )

    # Notes
    notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Auto-generation and review tracking
    is_auto_generated: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Whether this slip was auto-generated by scheduled task",
    )
    needs_review: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="Flag for slips requiring manual review before approval",
    )
    review_reasons: Mapped[Optional[list]] = mapped_column(
        JSON,
        nullable=True,
        comment="List of reasons why review is needed (e.g., attendance gaps, proration)",
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True,
        comment="When the slip was reviewed/acknowledged",
    )
    reviewed_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
        comment="Who reviewed/acknowledged the slip",
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
    employee: Mapped["Employee"] = relationship(
        "Employee",
        foreign_keys=[employee_id],
    )
    salary_structure: Mapped[Optional["SalaryStructure"]] = relationship(
        "SalaryStructure",
        foreign_keys=[structure_id],
    )
    cost_center: Mapped[Optional["CostCenter"]] = relationship(
        "CostCenter",
        foreign_keys=[cost_center_id],
    )
    journal_entry: Mapped[Optional["JournalEntry"]] = relationship(
        "JournalEntry",
        foreign_keys=[journal_entry_id],
    )
    payroll_entry: Mapped[Optional["PayrollEntry"]] = relationship(
        "PayrollEntry",
        foreign_keys=[payroll_entry_id],
        back_populates="salary_slips",
    )
    earnings: Mapped[list["SalarySlipEarning"]] = relationship(
        "SalarySlipEarning",
        back_populates="salary_slip",
        cascade="all, delete-orphan",
        order_by="SalarySlipEarning.display_order",
    )
    deductions: Mapped[list["SalarySlipDeduction"]] = relationship(
        "SalarySlipDeduction",
        back_populates="salary_slip",
        cascade="all, delete-orphan",
        order_by="SalarySlipDeduction.display_order",
    )

    @property
    def status_label(self) -> str:
        """Human-readable status for templates."""
        labels = {
            SalarySlipStatus.DRAFT: "Draft",
            SalarySlipStatus.SUBMITTED: "Submitted",
            SalarySlipStatus.APPROVED: "Approved",
            SalarySlipStatus.POSTED: "Posted",
            SalarySlipStatus.PAID: "Paid",
            SalarySlipStatus.CANCELLED: "Cancelled",
        }
        return labels.get(self.status, "Draft")

    @property
    def review_status_label(self) -> str:
        """Human-readable review status for templates."""
        if not self.needs_review:
            return "Ready"
        if self.reviewed_at:
            return "Reviewed"
        return "Needs Review"

    @property
    def review_badge_class(self) -> str:
        """CSS badge class for review status."""
        if not self.needs_review:
            return "bg-green-100 text-green-800"
        if self.reviewed_at:
            return "bg-blue-100 text-blue-800"
        return "bg-yellow-100 text-yellow-800"

    def __repr__(self) -> str:
        return f"<SalarySlip {self.slip_number} - {self.employee_name}: {self.net_pay}>"


class SalarySlipEarning(Base):
    """
    Salary Slip Earning - earning line on a salary slip.
    """

    __tablename__ = "salary_slip_earning"
    __table_args__ = (
        Index("idx_slip_earning_slip", "slip_id"),
        {"schema": "payroll"},
    )

    line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    slip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payroll.salary_slip.slip_id", ondelete="CASCADE"),
        nullable=False,
    )
    component_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payroll.salary_component.component_id"),
        nullable=False,
    )

    # Component info (denormalized)
    component_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    abbr: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
    )

    # Amounts
    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
    )
    default_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
        comment="Original amount before adjustments",
    )
    additional_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
        comment="One-time additions",
    )
    year_to_date: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
    )

    # Flags
    statistical_component: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    do_not_include_in_total: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    # Display
    display_order: Mapped[int] = mapped_column(default=0)

    # Relationships
    salary_slip: Mapped["SalarySlip"] = relationship(
        "SalarySlip",
        back_populates="earnings",
    )
    component: Mapped["SalaryComponent"] = relationship(
        "SalaryComponent",
        foreign_keys=[component_id],
    )


class SalarySlipDeduction(Base):
    """
    Salary Slip Deduction - deduction line on a salary slip.
    """

    __tablename__ = "salary_slip_deduction"
    __table_args__ = (
        Index("idx_slip_deduction_slip", "slip_id"),
        {"schema": "payroll"},
    )

    line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    slip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payroll.salary_slip.slip_id", ondelete="CASCADE"),
        nullable=False,
    )
    component_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payroll.salary_component.component_id"),
        nullable=False,
    )

    # Component info (denormalized)
    component_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    abbr: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
    )

    # Amounts
    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
    )
    default_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
        comment="Original amount before adjustments",
    )
    additional_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
        comment="One-time additions",
    )
    year_to_date: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
    )

    # Flags
    statistical_component: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
    do_not_include_in_total: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    # Display
    display_order: Mapped[int] = mapped_column(default=0)

    # Relationships
    salary_slip: Mapped["SalarySlip"] = relationship(
        "SalarySlip",
        back_populates="deductions",
    )
    component: Mapped["SalaryComponent"] = relationship(
        "SalaryComponent",
        foreign_keys=[component_id],
    )
