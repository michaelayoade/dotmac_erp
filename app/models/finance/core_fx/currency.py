"""
Currency Model - Core FX.
ISO 4217 currency codes.
"""

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Currency(Base):
    """
    ISO 4217 currency entity.
    """

    __tablename__ = "currency"
    __table_args__ = {"schema": "core_fx"}

    currency_code: Mapped[str] = mapped_column(
        String(3),
        primary_key=True,
        comment="ISO 4217 code",
    )
    currency_name: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(10), nullable=True)
    decimal_places: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_crypto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
