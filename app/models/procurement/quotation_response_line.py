"""
Quotation Response Line Model - proc Schema.

Line items within a vendor's quotation response.
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
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.procurement.quotation_response import QuotationResponse


class QuotationResponseLine(Base):
    """
    Line item in a vendor's quotation response.

    Contains per-item pricing and delivery details.
    """

    __tablename__ = "quotation_response_line"
    __table_args__ = (
        Index("idx_proc_quot_resp_line_response", "response_id"),
        {"schema": "proc"},
    )

    line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    response_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("proc.quotation_response.response_id", ondelete="CASCADE"),
        nullable=False,
    )
    requisition_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Links to original requisition line",
    )
    line_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )
    unit_price: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )
    line_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )
    delivery_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    # Relationships
    response: Mapped["QuotationResponse"] = relationship(
        "QuotationResponse",
        back_populates="lines",
    )
