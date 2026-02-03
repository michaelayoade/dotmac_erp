"""
Asset Component Model - FA Schema.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AssetComponent(Base):
    """
    Component of a fixed asset for component accounting (IAS 16).
    """

    __tablename__ = "asset_component"
    __table_args__ = (
        UniqueConstraint("asset_id", "component_code", name="uq_asset_component"),
        {"schema": "fa"},
    )

    component_id: Mapped[uuid.UUID] = mapped_column(
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

    component_code: Mapped[str] = mapped_column(String(30), nullable=False)
    component_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Cost allocation
    cost_allocation: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    cost_percentage: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)

    # Depreciation parameters (may differ from parent asset)
    depreciation_method: Mapped[str] = mapped_column(String(30), nullable=False)
    useful_life_months: Mapped[int] = mapped_column(Numeric(10, 0), nullable=False)
    remaining_life_months: Mapped[int] = mapped_column(Numeric(10, 0), nullable=False)
    residual_value: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)

    # Current values
    accumulated_depreciation: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    net_book_value: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    # Replacement tracking
    last_replacement_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    next_replacement_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

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
