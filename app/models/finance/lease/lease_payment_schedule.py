"""
Lease Payment Schedule Model - Lease Schema.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PaymentStatus(str, enum.Enum):
    SCHEDULED = "SCHEDULED"
    INVOICED = "INVOICED"
    PAID = "PAID"
    OVERDUE = "OVERDUE"
    CANCELLED = "CANCELLED"


class LeasePaymentSchedule(Base):
    """
    Individual lease payment schedule line.
    """

    __tablename__ = "lease_payment_schedule"
    __table_args__ = {"schema": "lease"}

    schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    lease_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lease.lease_contract.lease_id"),
        nullable=False,
    )
    liability_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("lease.lease_liability.liability_id"),
        nullable=False,
    )

    # Payment sequence
    payment_number: Mapped[int] = mapped_column(Integer, nullable=False)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    fiscal_period_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Payment breakdown
    total_payment: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    principal_portion: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    interest_portion: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    variable_payment: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )

    # Balance after payment
    opening_liability_balance: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )
    closing_liability_balance: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )

    # Index adjustment
    is_index_adjusted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    index_adjustment_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Status
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="lease_payment_status"),
        nullable=False,
        default=PaymentStatus.SCHEDULED,
    )

    # Actual payment tracking
    actual_payment_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    actual_payment_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    payment_reference: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    invoice_reference: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Journal entries
    interest_journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    payment_journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

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
