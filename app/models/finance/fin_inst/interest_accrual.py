"""
Interest Accrual Model - Financial Instruments Schema.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class InterestAccrual(Base):
    """
    Interest accrual for financial instruments.
    """

    __tablename__ = "interest_accrual"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id",
            "fiscal_period_id",
            name="uq_interest_accrual",
        ),
        {"schema": "fin_inst"},
    )

    accrual_id: Mapped[uuid.UUID] = mapped_column(
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

    # Accrual period
    accrual_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    accrual_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    days_in_period: Mapped[int] = mapped_column(Numeric(10, 0), nullable=False)

    # Principal basis
    principal_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)

    # Interest calculation
    effective_interest_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    interest_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    # Premium/discount amortization
    premium_discount_amortization: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    effective_interest_income: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    # Functional currency
    exchange_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 10), nullable=True)
    functional_currency_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    # Cash received/paid
    cash_interest: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    interest_receivable_movement: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )

    # Journal entry
    journal_entry_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
