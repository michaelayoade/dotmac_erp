"""
Report Instance Model - Reporting Schema.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ReportStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    GENERATING = "GENERATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ReportInstance(Base):
    """
    Generated report instance.
    """

    __tablename__ = "report_instance"
    __table_args__ = (
        Index("idx_rpt_instance_def", "report_def_id"),
        Index("idx_rpt_instance_date", "generated_at"),
        {"schema": "rpt"},
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    report_def_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rpt.report_definition.report_def_id"),
        nullable=False,
    )
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rpt.report_schedule.schedule_id"),
        nullable=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    # Report parameters used
    fiscal_period_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    parameters_used: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Output
    output_format: Mapped[str] = mapped_column(String(20), nullable=False)
    output_file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    output_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Generation status
    status: Mapped[ReportStatus] = mapped_column(
        Enum(ReportStatus, name="report_status"),
        nullable=False,
        default=ReportStatus.QUEUED,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timing
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    generation_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Audit
    generated_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
