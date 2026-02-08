"""
Purchase Requisition Line Model - proc Schema.

Line items within a purchase requisition.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.procurement.base import ProcurementBaseMixin

if TYPE_CHECKING:
    from app.models.procurement.purchase_requisition import PurchaseRequisition


class PurchaseRequisitionLine(Base, ProcurementBaseMixin):
    """
    Line item in a purchase requisition.

    Specifies quantity, estimated price, and delivery requirements
    for a single item being requested.
    """

    __tablename__ = "purchase_requisition_line"
    __table_args__ = (
        Index("idx_proc_req_line_requisition", "requisition_id"),
        {"schema": "proc"},
    )

    line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    requisition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("proc.purchase_requisition.requisition_id", ondelete="CASCADE"),
        nullable=False,
    )
    line_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="inv.item catalog item",
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )
    uom: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment="Unit of measure",
    )
    estimated_unit_price: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )
    estimated_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )
    expense_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    delivery_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    # Relationships
    requisition: Mapped["PurchaseRequisition"] = relationship(
        "PurchaseRequisition",
        back_populates="lines",
    )
