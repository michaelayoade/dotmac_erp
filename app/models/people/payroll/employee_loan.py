"""
Employee Loan Model - Payroll Schema.

Tracks active loans for employees with repayment schedules.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
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

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee
    from app.models.people.payroll.loan_type import LoanType
    from app.models.people.payroll.loan_repayment import LoanRepayment


class LoanStatus(str, enum.Enum):
    """Loan lifecycle status."""

    DRAFT = "DRAFT"  # Application in progress
    PENDING = "PENDING"  # Awaiting approval
    APPROVED = "APPROVED"  # Approved, awaiting disbursement
    DISBURSED = "DISBURSED"  # Active loan, repayment in progress
    COMPLETED = "COMPLETED"  # Fully repaid
    WRITTEN_OFF = "WRITTEN_OFF"  # Bad debt written off
    CANCELLED = "CANCELLED"  # Cancelled before disbursement
    REJECTED = "REJECTED"  # Application rejected


class EmployeeLoan(Base):
    """
    Employee Loan - Active loan for an employee.

    Tracks loan amount, repayment schedule, and outstanding balance.
    Integrates with payroll for automatic deductions.
    """

    __tablename__ = "employee_loan"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "loan_number",
            name="uq_employee_loan_number",
        ),
        Index("idx_employee_loan_employee", "employee_id"),
        Index("idx_employee_loan_status", "organization_id", "status"),
        Index("idx_employee_loan_active", "employee_id", "status"),
        {"schema": "payroll"},
    )

    loan_id: Mapped[uuid.UUID] = mapped_column(
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

    # Loan identification
    loan_number: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        comment="Auto-generated loan number, e.g., LOAN-2026-00001",
    )

    # Employee reference
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )

    # Loan type reference
    loan_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payroll.loan_type.loan_type_id"),
        nullable=False,
    )

    # Loan amounts
    principal_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        comment="Original loan amount",
    )
    interest_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        default=Decimal("0"),
        comment="Annual interest rate (percentage)",
    )
    total_interest: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
        comment="Total interest over loan term",
    )
    total_repayable: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        comment="Principal + interest = total to repay",
    )

    # Repayment schedule
    tenure_months: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Repayment period in months",
    )
    monthly_installment: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        comment="Fixed monthly repayment amount",
    )
    installments_paid: Mapped[int] = mapped_column(
        Integer,
        default=0,
        comment="Number of installments already paid",
    )

    # Balance tracking
    principal_paid: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
        comment="Total principal repaid so far",
    )
    interest_paid: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
        comment="Total interest paid so far",
    )
    outstanding_balance: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        comment="Remaining amount to be repaid",
    )

    # Key dates
    application_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        default=date.today,
    )
    approval_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    disbursement_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    first_repayment_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
        comment="Date of first scheduled repayment",
    )
    completion_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
        comment="Date when loan was fully repaid",
    )

    # Status
    status: Mapped[LoanStatus] = mapped_column(
        Enum(LoanStatus, name="loan_status"),
        default=LoanStatus.DRAFT,
    )

    # Approval workflow
    approved_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
    )
    rejection_reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Disbursement tracking
    disbursement_reference: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Payment reference for disbursement",
    )
    disbursed_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
    )

    # Notes
    purpose: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Purpose of the loan",
    )
    notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
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
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
    )

    # Relationships
    employee: Mapped["Employee"] = relationship(
        "Employee",
        foreign_keys=[employee_id],
    )
    loan_type: Mapped["LoanType"] = relationship(
        "LoanType",
        foreign_keys=[loan_type_id],
    )
    repayments: Mapped[list["LoanRepayment"]] = relationship(
        "LoanRepayment",
        back_populates="loan",
        order_by="LoanRepayment.repayment_date",
        cascade="all, delete-orphan",
    )

    @property
    def is_active(self) -> bool:
        """Whether the loan is active (has outstanding balance)."""
        return self.status == LoanStatus.DISBURSED and self.outstanding_balance > 0

    @property
    def remaining_installments(self) -> int:
        """Number of installments remaining."""
        return self.tenure_months - self.installments_paid

    @property
    def employee_name(self) -> str:
        """Get employee name for display."""
        if self.employee:
            return self.employee.full_name
        return ""

    def __repr__(self) -> str:
        return f"<EmployeeLoan {self.loan_number}: {self.principal_amount} ({self.status.value})>"
