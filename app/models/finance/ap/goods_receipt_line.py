"""
Goods Receipt Line Model - AP Schema.
"""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


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

    quantity_received: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    quantity_accepted: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    quantity_rejected: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    location_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    lot_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    serial_numbers: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text), nullable=True)

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
