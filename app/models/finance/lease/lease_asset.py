"""
Lease Asset (Right-of-Use Asset) Model - Lease Schema.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class LeaseAsset(Base):
    """
    Right-of-use asset for a lease (IFRS 16).
    """

    __tablename__ = "lease_asset"
    __table_args__ = {"schema": "lease"}

    asset_id: Mapped[uuid.UUID] = mapped_column(
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
    lease_liability_at_commencement: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )
    lease_payments_at_commencement: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    initial_direct_costs: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    restoration_obligation: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    lease_incentives_deducted: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    initial_rou_asset_value: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )

    # Depreciation parameters
    depreciation_method: Mapped[str] = mapped_column(String(30), nullable=False)
    useful_life_months: Mapped[int] = mapped_column(Numeric(10, 0), nullable=False)
    residual_value: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )

    # Current values
    accumulated_depreciation: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    impairment_losses: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    revaluation_adjustments: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    modification_adjustments: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    carrying_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    # Accounts
    rou_asset_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    accumulated_depreciation_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    depreciation_expense_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    # Last depreciation
    last_depreciation_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    last_depreciation_period_id: Mapped[Optional[uuid.UUID]] = mapped_column(
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
