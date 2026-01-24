"""
Instrument Valuation Model - Financial Instruments Schema.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class InstrumentValuation(Base):
    """
    Period-end valuation of financial instrument (IFRS 9).
    """

    __tablename__ = "instrument_valuation"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "fiscal_period_id",
            name="uq_instrument_valuation",
        ),
        {"schema": "fin_inst"},
    )

    valuation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    instrument_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fin_inst.financial_instrument.instrument_id"),
        nullable=False,
    )
    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.fiscal_period.fiscal_period_id"),
        nullable=False,
    )
    valuation_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Prior period values
    amortized_cost_opening: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    fair_value_opening: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 6), nullable=True)
    loss_allowance_opening: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    ecl_stage_opening: Mapped[int] = mapped_column(Numeric(1, 0), nullable=False)

    # Current period movements
    interest_accrued: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    premium_discount_amortized: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    principal_repayments: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    ecl_movement: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)

    # Fair value measurement
    fair_value_closing: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 6), nullable=True)
    fair_value_change: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 6), nullable=True)
    fair_value_level: Mapped[Optional[int]] = mapped_column(Numeric(1, 0), nullable=True)
    valuation_technique: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    key_inputs: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Closing values
    amortized_cost_closing: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    loss_allowance_closing: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    carrying_amount_closing: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    ecl_stage_closing: Mapped[int] = mapped_column(Numeric(1, 0), nullable=False)

    # P&L and OCI impact
    interest_income_pl: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    fv_change_pl: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    fv_change_oci: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    ecl_expense_pl: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)

    # Functional currency translation
    exchange_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 10), nullable=True)
    functional_currency_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    translation_difference: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)

    # Journal entries
    valuation_journal_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
