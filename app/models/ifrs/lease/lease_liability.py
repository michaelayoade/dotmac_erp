"""
Lease Liability Model - Lease Schema.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class LeaseLiability(Base):
    """
    Lease liability for a lease (IFRS 16).
    """

    __tablename__ = "lease_liability"
    __table_args__ = {"schema": "lease"}

    liability_id: Mapped[uuid.UUID] = mapped_column(
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

    # Initial measurement
    initial_measurement_date: Mapped[date] = mapped_column(Date, nullable=False)
    initial_liability_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    # PV of lease payments
    pv_fixed_payments: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    pv_variable_payments: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    pv_residual_guarantee: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    pv_purchase_option: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    pv_termination_penalties: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Discount rate
    discount_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)

    # Current balance
    current_liability_balance: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    # Interest tracking
    total_interest_expense: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    total_payments_made: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Modification adjustments
    modification_adjustments: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Classification
    current_portion: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    non_current_portion: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)

    # Accounts
    lease_liability_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    interest_expense_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    # Last interest accrual
    last_interest_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    last_interest_period_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

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
