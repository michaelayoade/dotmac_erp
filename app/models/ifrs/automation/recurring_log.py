"""
Recurring Log Model.

Tracks generation history for recurring templates.
"""
import enum
import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class RecurringLogStatus(str, enum.Enum):
    """Status of a generation attempt."""
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class RecurringLog(Base):
    """
    Log entry for recurring template generation.

    Tracks each generation attempt with status and result.
    """

    __tablename__ = "recurring_log"
    __table_args__ = (
        Index("idx_recurring_log_template", "template_id"),
        Index("idx_recurring_log_generated_at", "generated_at"),
        Index("idx_recurring_log_status", "status"),
        {"schema": "automation"},
    )

    log_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("automation.recurring_template.template_id", ondelete="CASCADE"),
        nullable=False,
    )

    # Scheduled vs actual dates
    scheduled_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="The date this was scheduled to run",
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Result
    status: Mapped[RecurringLogStatus] = mapped_column(
        Enum(RecurringLogStatus, name="recurring_log_status"),
        nullable=False,
    )

    # Generated entity reference
    generated_entity_type: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )
    generated_entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    generated_entity_number: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="Document number of generated entity",
    )

    # Error details
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Skip reason (if skipped)
    skip_reason: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # Relationship
    template: Mapped["RecurringTemplate"] = relationship(
        "RecurringTemplate",
        back_populates="logs",
    )


# Forward reference
from app.models.ifrs.automation.recurring_template import RecurringTemplate  # noqa: E402
