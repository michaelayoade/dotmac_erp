"""
Report Schedule Model - Reporting Schema.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ScheduleFrequency(str, enum.Enum):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    ANNUALLY = "ANNUALLY"
    ON_DEMAND = "ON_DEMAND"
    PERIOD_END = "PERIOD_END"


class ReportSchedule(Base):
    """
    Scheduled report configuration.
    """

    __tablename__ = "report_schedule"
    __table_args__ = {"schema": "rpt"}

    schedule_id: Mapped[uuid.UUID] = mapped_column(
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
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    schedule_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    frequency: Mapped[ScheduleFrequency] = mapped_column(
        Enum(ScheduleFrequency, name="schedule_frequency"),
        nullable=False,
    )

    # Schedule details
    cron_expression: Mapped[str | None] = mapped_column(String(100), nullable=True)
    day_of_week: Mapped[int | None] = mapped_column(nullable=True)
    day_of_month: Mapped[int | None] = mapped_column(nullable=True)
    time_of_day: Mapped[str | None] = mapped_column(String(10), nullable=True)
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="UTC")

    # Report parameters
    report_parameters: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_format: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PDF"
    )

    # Distribution
    email_recipients: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    storage_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    retention_days: Mapped[int | None] = mapped_column(nullable=True)

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )
