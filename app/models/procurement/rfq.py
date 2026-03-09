"""
Request for Quotation Model - proc Schema.

RFQ sent to vendors for competitive bidding.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Date,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import settings
from app.db import Base
from app.models.procurement.base import ProcurementBaseMixin
from app.models.procurement.enums import ProcurementMethod, RFQStatus

if TYPE_CHECKING:
    from app.models.procurement.quotation_response import QuotationResponse
    from app.models.procurement.rfq_invitation import RFQInvitation


class RequestForQuotation(Base, ProcurementBaseMixin):
    """
    Request for Quotation (RFQ).

    Issued to vendors to solicit competitive bids for goods,
    works, or services as required by PPA 2007.
    """

    __tablename__ = "request_for_quotation"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "rfq_number",
            name="uq_proc_rfq_org_number",
        ),
        Index("idx_proc_rfq_status", "organization_id", "status"),
        Index("idx_proc_rfq_closing", "organization_id", "closing_date"),
        {"schema": "proc"},
    )

    rfq_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    rfq_number: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    rfq_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    closing_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="Bid submission deadline",
    )
    status: Mapped[RFQStatus] = mapped_column(
        default=RFQStatus.DRAFT,
    )
    procurement_method: Mapped[ProcurementMethod] = mapped_column(
        default=ProcurementMethod.OPEN_COMPETITIVE,
    )
    requisition_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Source purchase requisition",
    )
    plan_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Link to procurement plan item",
    )
    evaluation_criteria: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="[{name, weight, description}]",
    )
    terms_and_conditions: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    estimated_value: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default=settings.default_functional_currency_code,
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    # Relationships
    invitations: Mapped[list["RFQInvitation"]] = relationship(
        "RFQInvitation",
        back_populates="rfq",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    responses: Mapped[list["QuotationResponse"]] = relationship(
        "QuotationResponse",
        back_populates="rfq",
        lazy="selectin",
    )
