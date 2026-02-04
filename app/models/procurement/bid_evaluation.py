"""
Bid Evaluation Model - proc Schema.

Evaluation record comparing vendor bids for an RFQ.
"""

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.procurement.base import ProcurementBaseMixin
from app.models.procurement.enums import EvaluationStatus

if TYPE_CHECKING:
    from app.models.procurement.bid_evaluation_score import BidEvaluationScore


class BidEvaluation(Base, ProcurementBaseMixin):
    """
    Bid evaluation record.

    Compares vendor bids based on defined criteria and produces
    a recommendation for contract award.
    """

    __tablename__ = "bid_evaluation"
    __table_args__ = (
        Index("idx_proc_eval_rfq", "rfq_id"),
        Index("idx_proc_eval_status", "organization_id", "status"),
        {"schema": "proc"},
    )

    evaluation_id: Mapped[uuid.UUID] = mapped_column(
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
    evaluation_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    status: Mapped[EvaluationStatus] = mapped_column(
        default=EvaluationStatus.DRAFT,
    )
    recommended_supplier_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Winning vendor (ap.supplier)",
    )
    recommended_response_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Winning bid (quotation_response)",
    )
    evaluation_report: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Summary/justification",
    )
    approval_request_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Link to approval workflow",
    )
    evaluated_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    scores: Mapped[List["BidEvaluationScore"]] = relationship(
        "BidEvaluationScore",
        back_populates="evaluation",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
