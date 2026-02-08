"""
Exchange Rate Model - Core FX.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ExchangeRateSource(str, enum.Enum):
    MANUAL = "MANUAL"
    ECB = "ECB"
    REUTERS = "REUTERS"
    BLOOMBERG = "BLOOMBERG"
    API = "API"


class ExchangeRate(Base):
    """
    Exchange rate for currency conversion.
    """

    __tablename__ = "exchange_rate"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "from_currency_code",
            "to_currency_code",
            "rate_type_id",
            "effective_date",
            name="uq_exchange_rate",
        ),
        Index(
            "idx_fx_rate_lookup",
            "organization_id",
            "from_currency_code",
            "to_currency_code",
            "rate_type_id",
            "effective_date",
        ),
        {"schema": "core_fx"},
    )

    exchange_rate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
    )

    from_currency_code: Mapped[str] = mapped_column(
        String(3),
        ForeignKey("core_fx.currency.currency_code"),
        nullable=False,
    )
    to_currency_code: Mapped[str] = mapped_column(
        String(3),
        ForeignKey("core_fx.currency.currency_code"),
        nullable=False,
    )
    rate_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_fx.exchange_rate_type.rate_type_id"),
        nullable=False,
    )

    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    exchange_rate: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)

    source: Mapped[ExchangeRateSource | None] = mapped_column(
        Enum(ExchangeRateSource, name="exchange_rate_source"),
        nullable=True,
    )

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    @property
    def inverse_rate(self) -> Decimal:
        """Calculate inverse rate."""
        return Decimal(1) / self.exchange_rate if self.exchange_rate else Decimal(0)
