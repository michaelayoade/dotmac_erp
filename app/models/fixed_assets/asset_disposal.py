"""
Asset Disposal Model - FA Schema.
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
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DisposalType(str, enum.Enum):
    SALE = "SALE"
    SCRAPPING = "SCRAPPING"
    DONATION = "DONATION"
    THEFT = "THEFT"
    INSURANCE_CLAIM = "INSURANCE_CLAIM"
    TRADE_IN = "TRADE_IN"
    TRANSFER = "TRANSFER"


class AssetDisposal(Base):
    """
    Asset disposal record.
    """

    __tablename__ = "asset_disposal"
    __table_args__ = {"schema": "fa"}

    disposal_id: Mapped[uuid.UUID] = mapped_column(
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

    disposal_date: Mapped[date] = mapped_column(Date, nullable=False)
    disposal_type: Mapped[DisposalType] = mapped_column(
        Enum(DisposalType, name="disposal_type"),
        nullable=False,
    )

    # Values at disposal
    cost_at_disposal: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    accumulated_depreciation_at_disposal: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )
    net_book_value_at_disposal: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )

    # Disposal proceeds
    disposal_proceeds: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    costs_of_disposal: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    net_proceeds: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )

    # Gain/Loss calculation
    gain_loss_on_disposal: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )

    # Buyer/Recipient details
    buyer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    buyer_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Reason and documentation
    disposal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    authorization_reference: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )

    # Trade-in details
    trade_in_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Insurance claim details
    insurance_claim_reference: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    insurance_proceeds: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6), nullable=True
    )

    # Accounting
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # SoD tracking
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
