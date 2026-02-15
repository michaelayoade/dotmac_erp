"""
Coach Report model.

Stores periodic digests (weekly/monthly) generated for audiences/targets.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import JSON, Date, DateTime, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

_JSON = JSON().with_variant(JSONB, "postgresql")


class CoachReport(Base):
    __tablename__ = "coach_report"
    __table_args__ = (
        Index("idx_coach_report_org", "organization_id"),
        Index("idx_coach_report_org_target", "organization_id", "target_employee_id"),
        Index(
            "idx_coach_report_period", "organization_id", "period_start", "period_end"
        ),
    )

    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )

    # Target
    audience: Mapped[str] = mapped_column(String(30), nullable=False)
    target_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    report_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Period
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    # Content
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    executive_summary: Mapped[str] = mapped_column(Text, nullable=False)
    sections: Mapped[list[dict]] = mapped_column(_JSON, nullable=False, default=list)
    key_metrics: Mapped[dict] = mapped_column(_JSON, nullable=False, default=dict)
    recommendations: Mapped[list[dict]] = mapped_column(
        _JSON, nullable=False, default=list
    )

    # Metadata
    model_used: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    generation_time_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
