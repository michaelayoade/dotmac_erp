"""
Depreciation Schedule Model - FA Schema.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DepreciationSchedule(Base):
    """
    Individual depreciation entry for an asset in a run.
    """

    __tablename__ = "depreciation_schedule"
    __table_args__ = (
        UniqueConstraint("run_id", "asset_id", name="uq_depreciation_schedule"),
        {"schema": "fa"},
    )

    schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fa.depreciation_run.run_id"),
        nullable=False,
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fa.asset.asset_id"),
        nullable=False,
    )

    # Component (if component accounting)
    component_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fa.asset_component.component_id"),
        nullable=True,
    )

    # Values before depreciation
    cost_basis: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    accumulated_depreciation_opening: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )
    net_book_value_opening: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )

    # Depreciation calculation
    depreciation_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    # Values after depreciation
    accumulated_depreciation_closing: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )
    net_book_value_closing: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )

    # Remaining life tracking
    remaining_life_months_opening: Mapped[int] = mapped_column(
        Numeric(10, 0), nullable=False
    )
    remaining_life_months_closing: Mapped[int] = mapped_column(
        Numeric(10, 0), nullable=False
    )

    # Accounting references
    expense_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    accumulated_depreciation_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
