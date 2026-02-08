"""
Bid Evaluation Score Model - proc Schema.

Per-vendor, per-criterion scores in a bid evaluation.
"""

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.procurement.bid_evaluation import BidEvaluation


class BidEvaluationScore(Base):
    """
    Individual criterion score for a vendor's bid.

    Tracks score, weight, and weighted score for each
    evaluation criterion per vendor response.
    """

    __tablename__ = "bid_evaluation_score"
    __table_args__ = (
        Index("idx_proc_eval_score_eval", "evaluation_id"),
        Index("idx_proc_eval_score_response", "response_id"),
        {"schema": "proc"},
    )

    score_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("proc.bid_evaluation.evaluation_id", ondelete="CASCADE"),
        nullable=False,
    )
    response_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="Which vendor's bid (quotation_response)",
    )
    criterion_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment='e.g. "Price", "Technical", "Experience"',
    )
    weight: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        comment="Percentage weight",
    )
    score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        comment="Score 0-100",
    )
    weighted_score: Mapped[Decimal] = mapped_column(
        Numeric(10, 4),
        nullable=False,
        comment="weight * score / 100",
    )
    comments: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    evaluation: Mapped["BidEvaluation"] = relationship(
        "BidEvaluation",
        back_populates="scores",
    )
