"""
Purchase Order Line Model - AP Schema.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class PurchaseOrderLine(Base):
    """
    Purchase order line item.
    """

    __tablename__ = "purchase_order_line"
    __table_args__ = (
        UniqueConstraint("po_id", "line_number", name="uq_po_line"),
        {"schema": "ap"},
    )

    line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    po_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ap.purchase_order.po_id"),
        nullable=False,
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)

    item_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # Quantities
    quantity_ordered: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    quantity_received: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)
    quantity_invoiced: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)

    # Pricing
    unit_price: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    line_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)

    # Tax
    tax_code_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)

    # Accounting
    expense_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    asset_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="For capitalized items",
    )

    # Dimensions
    cost_center_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    segment_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)

    delivery_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    purchase_order: Mapped["PurchaseOrder"] = relationship(
        "PurchaseOrder",
        back_populates="lines",
    )


# Forward reference
from app.models.finance.ap.purchase_order import PurchaseOrder  # noqa: E402
