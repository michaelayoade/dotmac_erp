"""
Hedge Effectiveness Model - Financial Instruments Schema.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class HedgeEffectiveness(Base):
    """
    Hedge effectiveness test results (IFRS 9).
    """

    __tablename__ = "hedge_effectiveness"
    __table_args__ = {"schema": "fin_inst"}

    effectiveness_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    hedge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fin_inst.hedge_relationship.hedge_id"),
        nullable=False,
    )
    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.fiscal_period.fiscal_period_id"),
        nullable=False,
    )
    test_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Prospective (forward-looking) test
    prospective_test_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    prospective_test_result: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(10, 4),
        nullable=True,
    )
    prospective_test_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Retrospective test
    hedging_instrument_fv_change: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    hedged_item_fv_change: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    hedge_effectiveness_ratio: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    retrospective_test_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # Ineffectiveness
    hedge_ineffectiveness: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    ineffectiveness_recognized_pl: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Effective portion
    effective_portion: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    effective_portion_oci: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Reclassification from OCI to P&L
    reclassification_to_pl: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    # Basis adjustment (for cash flow hedges of forecast transactions)
    basis_adjustment: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)

    # Overall effectiveness
    is_highly_effective: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # Journal entries
    effectiveness_journal_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
