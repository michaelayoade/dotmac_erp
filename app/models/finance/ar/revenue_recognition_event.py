"""
Revenue Recognition Event Model - AR Schema.
IFRS 15 Revenue Recognition Events.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RevenueRecognitionEvent(Base):
    """
    Revenue recognition event for performance obligation.
    """

    __tablename__ = "revenue_recognition_event"
    __table_args__ = {"schema": "ar"}

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    obligation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ar.performance_obligation.obligation_id"),
        nullable=False,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    event_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        comment="SATISFACTION, PROGRESS_UPDATE, MODIFICATION, IMPAIRMENT",
    )

    progress_percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )
    amount_recognized: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    cumulative_recognized: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False
    )

    measurement_details: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )

    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    posting_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
