"""
Loan Repayment Model - Payroll Schema.

Tracks individual repayment transactions for employee loans.
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
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.people.payroll.employee_loan import EmployeeLoan
    from app.models.people.payroll.salary_slip import SalarySlip


class RepaymentType(str, enum.Enum):
    """Type of loan repayment."""

    PAYROLL_DEDUCTION = "PAYROLL_DEDUCTION"  # Automatic deduction from salary
    MANUAL_PAYMENT = "MANUAL_PAYMENT"  # Direct payment by employee
    PREPAYMENT = "PREPAYMENT"  # Early partial or full repayment
    WRITE_OFF = "WRITE_OFF"  # Bad debt write-off


class LoanRepayment(Base):
    """
    Loan Repayment - Individual repayment transaction.

    Records each payment toward an employee loan, whether via
    payroll deduction, manual payment, or prepayment.
    """

    __tablename__ = "loan_repayment"
    __table_args__ = (
        Index("idx_loan_repayment_loan", "loan_id"),
        Index("idx_loan_repayment_slip", "salary_slip_id"),
        Index("idx_loan_repayment_date", "repayment_date"),
        {"schema": "payroll"},
    )

    repayment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # Loan reference
    loan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payroll.employee_loan.loan_id", ondelete="CASCADE"),
        nullable=False,
    )

    # Repayment details
    repayment_type: Mapped[RepaymentType] = mapped_column(
        Enum(RepaymentType, name="repayment_type"),
        nullable=False,
    )
    repayment_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    # Amount breakdown
    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        comment="Total repayment amount",
    )
    principal_portion: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        comment="Portion applied to principal",
    )
    interest_portion: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
        comment="Portion applied to interest",
    )

    # Balance after this repayment
    balance_after: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        comment="Outstanding balance after this repayment",
    )

    # Payroll link (for PAYROLL_DEDUCTION type)
    salary_slip_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payroll.salary_slip.slip_id", ondelete="SET NULL"),
        nullable=True,
        comment="Link to salary slip if via payroll",
    )

    # Payment reference (for MANUAL_PAYMENT type)
    payment_reference: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Payment reference for manual payments",
    )
    payment_method: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="Payment method (bank transfer, cash, etc.)",
    )

    # Notes
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
    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
    )

    # Relationships
    loan: Mapped["EmployeeLoan"] = relationship(
        "EmployeeLoan",
        back_populates="repayments",
    )
    salary_slip: Mapped[Optional["SalarySlip"]] = relationship(
        "SalarySlip",
        foreign_keys=[salary_slip_id],
    )

    def __repr__(self) -> str:
        return f"<LoanRepayment {self.amount} on {self.repayment_date}>"


class SalarySlipLoanDeduction(Base):
    """
    Salary Slip Loan Deduction - Link between salary slip and loan deductions.

    Tracks which loans had deductions on a specific salary slip.
    """

    __tablename__ = "salary_slip_loan_deduction"
    __table_args__ = (
        Index("idx_slip_loan_slip", "slip_id"),
        Index("idx_slip_loan_loan", "loan_id"),
        {"schema": "payroll"},
    )

    deduction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # Salary slip reference
    slip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payroll.salary_slip.slip_id", ondelete="CASCADE"),
        nullable=False,
    )

    # Loan reference
    loan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payroll.employee_loan.loan_id", ondelete="CASCADE"),
        nullable=False,
    )

    # Deduction amount
    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )
    principal_portion: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )
    interest_portion: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        default=Decimal("0"),
    )

    # Repayment link
    repayment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payroll.loan_repayment.repayment_id", ondelete="SET NULL"),
        nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
