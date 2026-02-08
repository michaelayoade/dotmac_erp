"""
Currency Translation Adjustment Model - Core FX.
IAS 21 - Foreign Operations CTA.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class CTAAdjustmentType(str, enum.Enum):
    TRANSLATION = "TRANSLATION"
    HYPERINFLATION = "HYPERINFLATION"
    DISPOSAL = "DISPOSAL"


class CurrencyTranslationAdjustment(Base):
    """
    IAS 21 Currency Translation Adjustment for foreign operations.
    """

    __tablename__ = "currency_translation_adjustment"
    __table_args__ = {"schema": "core_fx"}

    adjustment_id: Mapped[uuid.UUID] = mapped_column(
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
    foreign_operation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
    )
    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    adjustment_type: Mapped[CTAAdjustmentType] = mapped_column(
        Enum(CTAAdjustmentType, name="cta_adjustment_type"),
        nullable=False,
    )

    functional_currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    presentation_currency_code: Mapped[str] = mapped_column(String(3), nullable=False)

    # Amounts
    net_investment_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    translation_difference: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )
    recycled_to_pnl: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    oci_balance: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
