"""
Goods Receipt Model - AP Schema.
"""
import enum
import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ReceiptStatus(str, enum.Enum):
    RECEIVED = "RECEIVED"
    INSPECTING = "INSPECTING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    PARTIAL = "PARTIAL"


class GoodsReceipt(Base):
    """
    Goods receipt header.
    """

    __tablename__ = "goods_receipt"
    __table_args__ = (
        UniqueConstraint("organization_id", "receipt_number", name="uq_receipt_number"),
        Index("idx_receipt_po", "po_id"),
        {"schema": "ap"},
    )

    receipt_id: Mapped[uuid.UUID] = mapped_column(
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
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ap.supplier.supplier_id"),
        nullable=False,
    )
    po_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ap.purchase_order.po_id"),
        nullable=False,
    )

    receipt_number: Mapped[str] = mapped_column(String(30), nullable=False)
    receipt_date: Mapped[date] = mapped_column(Date, nullable=False)

    status: Mapped[ReceiptStatus] = mapped_column(
        Enum(ReceiptStatus, name="receipt_status"),
        nullable=False,
        default=ReceiptStatus.RECEIVED,
    )

    received_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    warehouse_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    lines: Mapped[list["GoodsReceiptLine"]] = relationship(
        "GoodsReceiptLine",
        back_populates="goods_receipt",
        cascade="all, delete-orphan",
    )


# Forward reference
from app.models.finance.ap.goods_receipt_line import GoodsReceiptLine  # noqa: E402
