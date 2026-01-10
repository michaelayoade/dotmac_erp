"""
Asset Impairment Model - FA Schema.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AssetImpairment(Base):
    """
    Asset impairment record (IAS 36).
    """

    __tablename__ = "asset_impairment"
    __table_args__ = {"schema": "fa"}

    impairment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # Can be at asset level or CGU level
    asset_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fa.asset.asset_id"),
        nullable=True,
    )
    cgu_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fa.cash_generating_unit.cgu_id"),
        nullable=True,
    )

    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.fiscal_period.fiscal_period_id"),
        nullable=False,
    )
    impairment_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Pre-impairment values
    carrying_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    # Recoverable amount determination
    fair_value_less_costs_to_sell: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    value_in_use: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    recoverable_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    recoverable_amount_basis: Mapped[str] = mapped_column(String(30), nullable=False)

    # Impairment loss
    impairment_loss: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    # Goodwill allocation (for CGU impairment)
    goodwill_impairment: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    asset_impairment_allocated: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Value in use assumptions
    discount_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    growth_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    projection_period_years: Mapped[Optional[int]] = mapped_column(Numeric(5, 0), nullable=True)
    assumptions_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Reversal tracking
    is_reversal: Mapped[bool] = mapped_column(default=False)
    original_impairment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fa.asset_impairment.impairment_id"),
        nullable=True,
    )

    # Accounting
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
