"""
Goods Receipt Line Model - AP Schema.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class InspectionStatus(str, enum.Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"


class GoodsReceiptLine(Base):
    """
    Goods receipt line item.
    """

    __tablename__ = "goods_receipt_line"
    __table_args__ = (
        UniqueConstraint("receipt_id", "line_number", name="uq_receipt_line"),
        {"schema": "ap"},
    )

    line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    receipt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ap.goods_receipt.receipt_id"),
        nullable=False,
    )
    po_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ap.purchase_order_line.line_id"),
        nullable=False,
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)

    item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    quantity_received: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    quantity_accepted: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    quantity_rejected: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    inspection_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    inspection_status: Mapped[InspectionStatus] = mapped_column(
        Enum(InspectionStatus, name="inspection_status"),
        nullable=False,
        default=InspectionStatus.NOT_REQUIRED,
    )

    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    lot_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    serial_numbers: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    goods_receipt: Mapped["GoodsReceipt"] = relationship(
        "GoodsReceipt",
        back_populates="lines",
    )


# Forward reference
from app.models.finance.ap.goods_receipt import GoodsReceipt  # noqa: E402
