"""
Reporting Segment Model - Core Org.
IFRS 8 Operating Segments.
"""
import enum
import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SegmentType(str, enum.Enum):
    OPERATING = "OPERATING"
    GEOGRAPHICAL = "GEOGRAPHICAL"
    REPORTABLE = "REPORTABLE"


class ReportingSegment(Base):
    """
    IFRS 8 Operating Segments for segment reporting.
    """

    __tablename__ = "reporting_segment"
    __table_args__ = (
        UniqueConstraint("organization_id", "segment_code", name="uq_segment_code"),
        {"schema": "core_org"},
    )

    segment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
    )

    segment_code: Mapped[str] = mapped_column(String(20), nullable=False)
    segment_name: Mapped[str] = mapped_column(String(100), nullable=False)
    segment_type: Mapped[SegmentType] = mapped_column(
        Enum(SegmentType, name="segment_type"),
        nullable=False,
    )

    # IFRS 8 requirements
    chief_operating_decision_maker: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    is_reportable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    aggregation_criteria: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Effective dating
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    effective_from: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        server_default=func.current_date(),
    )
    effective_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )
