"""
Quotation Response Model - proc Schema.

Vendor bid/quotation in response to an RFQ.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import settings
from app.db import Base
from app.models.procurement.base import ProcurementBaseMixin
from app.models.procurement.enums import QuotationResponseStatus

if TYPE_CHECKING:
    from app.models.procurement.quotation_response_line import QuotationResponseLine
    from app.models.procurement.rfq import RequestForQuotation


class QuotationResponse(Base, ProcurementBaseMixin):
    """
    Vendor quotation/bid in response to an RFQ.

    Contains the vendor's pricing, delivery terms, and technical proposal.
    """

    __tablename__ = "quotation_response"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "response_number",
            name="uq_proc_quot_resp_org_number",
        ),
        Index("idx_proc_quot_resp_rfq", "rfq_id"),
        Index("idx_proc_quot_resp_supplier", "supplier_id"),
        Index("idx_proc_quot_resp_status", "organization_id", "status"),
        {"schema": "proc"},
    )

    response_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    rfq_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("proc.request_for_quotation.rfq_id"),
        nullable=False,
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="ap.supplier",
    )
    response_number: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    response_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )
    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default=settings.default_functional_currency_code,
    )
    delivery_period_days: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    validity_days: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Quote validity period in days",
    )
    technical_proposal: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    status: Mapped[QuotationResponseStatus] = mapped_column(
        default=QuotationResponseStatus.RECEIVED,
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Formal receipt timestamp",
    )

    # Relationships
    rfq: Mapped["RequestForQuotation"] = relationship(
        "RequestForQuotation",
        back_populates="responses",
    )
    lines: Mapped[list["QuotationResponseLine"]] = relationship(
        "QuotationResponseLine",
        back_populates="response",
        cascade="all, delete-orphan",
        order_by="QuotationResponseLine.line_number",
        lazy="selectin",
    )
