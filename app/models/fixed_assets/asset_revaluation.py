"""
Asset Revaluation Model - FA Schema.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AssetRevaluation(Base):
    """
    Asset revaluation record (IAS 16 revaluation model).
    """

    __tablename__ = "asset_revaluation"
    __table_args__ = {"schema": "fa"}

    revaluation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fa.asset.asset_id"),
        nullable=False,
    )
    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.fiscal_period.fiscal_period_id"),
        nullable=False,
    )

    revaluation_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Values before revaluation
    carrying_amount_before: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )
    accumulated_depreciation_before: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )

    # Revalued amounts
    fair_value: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    revaluation_surplus_or_deficit: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )

    # Values after revaluation
    carrying_amount_after: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )
    accumulated_depreciation_after: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )

    # Valuation details
    valuation_method: Mapped[str] = mapped_column(String(50), nullable=False)
    valuer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    valuer_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    valuation_basis: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Accounting entries
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Surplus recognized in equity vs P&L
    surplus_to_equity: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    deficit_to_pl: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )

    # Prior revaluation reversal
    prior_deficit_reversed: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    prior_surplus_reversed: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Audit
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
