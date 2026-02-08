"""
Exchange Rate Type Model - Core FX.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ExchangeRateType(Base):
    """
    Exchange rate type for different rate purposes.
    Standard types: SPOT, AVERAGE, CLOSING, HISTORICAL, BUDGET
    """

    __tablename__ = "exchange_rate_type"
    __table_args__ = (
        UniqueConstraint("organization_id", "type_code", name="uq_rate_type"),
        {"schema": "core_fx"},
    )

    rate_type_id: Mapped[uuid.UUID] = mapped_column(
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

    type_code: Mapped[str] = mapped_column(String(20), nullable=False)
    type_name: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
