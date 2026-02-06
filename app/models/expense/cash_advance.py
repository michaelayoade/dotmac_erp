"""
Cash Advance Model - Expense Schema.

Employee cash advances with settlement tracking.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
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
from app.models.mixins import AuditMixin, ERPNextSyncMixin, StatusTrackingMixin

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee


class CashAdvanceStatus(str, enum.Enum):
    """Cash advance workflow status."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DISBURSED = "DISBURSED"
    PARTIALLY_SETTLED = "PARTIALLY_SETTLED"
    FULLY_SETTLED = "FULLY_SETTLED"
    REFUNDED = "REFUNDED"
    CANCELLED = "CANCELLED"


class CashAdvance(Base, AuditMixin, StatusTrackingMixin, ERPNextSyncMixin):
    """
    Cash Advance - advance payment to employee.

    Tracks disbursement and settlement via expense claims.
    """

    __tablename__ = "cash_advance"
    __table_args__ = (
        Index("idx_cash_advance_employee", "employee_id"),
        Index("idx_cash_advance_status", "organization_id", "status"),
        {"schema": "expense"},
    )

    advance_id: Mapped[uuid.UUID] = mapped_column(
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

    # Reference
    advance_number: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        unique=True,
    )

    # Employee (optional for non-employee advances)
    employee_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )

    # Advance details
    request_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    purpose: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    # Amount
    requested_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )
    approved_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )
    currency_code: Mapped[str] = mapped_column(
        String(3),
        default="NGN",
    )

    # Settlement tracking
    amount_settled: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        default=Decimal("0.00"),
        comment="Total settled via expense claims",
    )
    amount_refunded: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        default=Decimal("0.00"),
        comment="Amount returned by employee",
    )

    # Dates
    expected_settlement_date: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    disbursed_on: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    settled_on: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )

    # Cost allocation
    cost_center_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.cost_center.cost_center_id"),
        nullable=True,
    )

    # GL Integration
    advance_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.account.account_id"),
        nullable=True,
        comment="Advance receivable account",
    )
    journal_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.journal_entry.journal_entry_id"),
        nullable=True,
        comment="GL entry for disbursement",
    )

    # Status
    status: Mapped[CashAdvanceStatus] = mapped_column(
        Enum(CashAdvanceStatus, name="cash_advance_status"),
        default=CashAdvanceStatus.DRAFT,
    )

    # Approval
    approver_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )
    approved_on: Mapped[Optional[date]] = mapped_column(
        Date,
        nullable=True,
    )
    rejection_reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Payment details
    payment_mode: Mapped[Optional[str]] = mapped_column(
        String(30),
        nullable=True,
        comment="BANK_TRANSFER, CASH, CHEQUE",
    )
    payment_reference: Mapped[Optional[str]] = mapped_column(
        String(100),
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
    employee: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[employee_id],
    )
    approver: Mapped[Optional["Employee"]] = relationship(
        "Employee",
        foreign_keys=[approver_id],
    )

    @property
    def outstanding_balance(self) -> Decimal:
        """Calculate outstanding advance balance."""
        disbursed = self.approved_amount or Decimal("0.00")
        return disbursed - self.amount_settled - self.amount_refunded

    @property
    def is_fully_settled(self) -> bool:
        """Check if advance is fully settled."""
        return self.outstanding_balance <= Decimal("0.00")

    def __repr__(self) -> str:
        return f"<CashAdvance {self.advance_number}: {self.status.value}>"
