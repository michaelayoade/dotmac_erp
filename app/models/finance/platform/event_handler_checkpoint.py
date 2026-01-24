"""
Event Handler Checkpoint - Tracks handler processing for idempotency.
Document 10: Event-Driven Architecture.
"""
import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class CheckpointStatus(str, enum.Enum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class EventHandlerCheckpoint(Base):
    """
    Tracks event handler processing for idempotency.
    Ensures each handler processes an event exactly once.
    """

    __tablename__ = "event_handler_checkpoint"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "handler_name",
            name="uq_checkpoint_event_handler",
        ),
        {"schema": "platform"},
    )

    checkpoint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("platform.event_outbox.event_id"),
        nullable=False,
    )
    handler_name: Mapped[str] = mapped_column(String(200), nullable=False)

    processed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    status: Mapped[CheckpointStatus] = mapped_column(
        Enum(CheckpointStatus, name="checkpoint_status"),
        nullable=False,
        default=CheckpointStatus.PENDING,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    event: Mapped["EventOutbox"] = relationship(
        "EventOutbox",
        foreign_keys=[event_id],
    )


# Import for type hints
from app.models.finance.platform.event_outbox import EventOutbox  # noqa: E402
