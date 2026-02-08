"""
RFQ Invitation Model - proc Schema.

Vendors invited to respond to an RFQ.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.procurement.rfq import RequestForQuotation


class RFQInvitation(Base):
    """
    Invitation record linking a vendor to an RFQ.

    Tracks which suppliers were invited and whether they responded.
    """

    __tablename__ = "rfq_invitation"
    __table_args__ = (
        Index("idx_proc_rfq_inv_rfq", "rfq_id"),
        Index("idx_proc_rfq_inv_supplier", "supplier_id"),
        {"schema": "proc"},
    )

    invitation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    rfq_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("proc.request_for_quotation.rfq_id", ondelete="CASCADE"),
        nullable=False,
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="ap.supplier",
    )
    invited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    responded: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    response_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    rfq: Mapped["RequestForQuotation"] = relationship(
        "RequestForQuotation",
        back_populates="invitations",
    )
