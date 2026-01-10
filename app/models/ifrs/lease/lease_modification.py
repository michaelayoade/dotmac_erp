"""
Lease Modification Model - Lease Schema.
"""
import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ModificationType(str, enum.Enum):
    SCOPE_INCREASE = "SCOPE_INCREASE"
    SCOPE_DECREASE = "SCOPE_DECREASE"
    TERM_EXTENSION = "TERM_EXTENSION"
    TERM_REDUCTION = "TERM_REDUCTION"
    PAYMENT_CHANGE = "PAYMENT_CHANGE"
    INDEX_ADJUSTMENT = "INDEX_ADJUSTMENT"
    REASSESSMENT = "REASSESSMENT"


class LeaseModification(Base):
    """
    Lease modification record (IFRS 16).
    """

    __tablename__ = "lease_modification"
    __table_args__ = {"schema": "lease"}

    modification_id: Mapped[uuid.UUID] = mapped_column(
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
    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.fiscal_period.fiscal_period_id"),
        nullable=False,
    )

    modification_date: Mapped[date] = mapped_column(Date, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    modification_type: Mapped[ModificationType] = mapped_column(
        Enum(ModificationType, name="lease_modification_type"),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Is this a separate lease? (IFRS 16.44)
    is_separate_lease: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Values before modification
    liability_before: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    rou_asset_before: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    remaining_lease_term_before: Mapped[int] = mapped_column(Numeric(10, 0), nullable=False)
    discount_rate_before: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)

    # Modification parameters
    new_lease_payments: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 6), nullable=True)
    revised_discount_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6), nullable=True)
    revised_lease_term_months: Mapped[Optional[int]] = mapped_column(Numeric(10, 0), nullable=True)

    # Values after modification
    liability_after: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    rou_asset_after: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    # Adjustment amounts
    liability_adjustment: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    rou_asset_adjustment: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    gain_loss_on_modification: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Journal entry
    journal_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Audit
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
