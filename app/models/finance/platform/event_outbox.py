"""
Event Outbox - Transactional outbox for reliable event delivery.
Document 10: Event-Driven Architecture.
"""

import enum
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class EventStatus(str, enum.Enum):
    PENDING = "PENDING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    DEAD = "DEAD"


class TerminalReason:
    """Well-known values for ``EventOutbox.terminal_reason``.

    A plain string column (not a PG enum) so new reasons stay additive
    without ``ALTER TYPE``. ``terminal_reason`` distinguishes *why* an
    event reached a terminal status:

    - on ``DEAD``: why it was dead-lettered (alertable);
    - on ``PUBLISHED``: ``DECLARED_NO_CONSEQUENCE`` records that the event
      settled as a documented no-op rather than an applied consequence.
    """

    UNSUPPORTED_EVENT = "unsupported_event"
    MAX_RETRIES_EXCEEDED = "max_retries_exceeded"
    DECLARED_NO_CONSEQUENCE = "declared_no_consequence"
    MISSING_ORGANIZATION_CONTEXT = "missing_organization_context"
    INVALID_PAYLOAD = "invalid_payload"
    MANUAL_DEAD_LETTER = "manual_dead_letter"


class EventOutbox(Base):
    """
    Transactional outbox for reliable event delivery.
    Events are written atomically with business transactions.
    """

    __tablename__ = "event_outbox"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_outbox_idempotency",
        ),
        Index("idx_outbox_pending", "status", "next_retry_at"),
        Index("idx_outbox_claim", "status", "lease_expires_at"),
        Index("idx_outbox_aggregate", "aggregate_type", "aggregate_id"),
        Index("idx_outbox_correlation", "correlation_id"),
        {"schema": "platform"},
    )

    # Identity
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # Timing
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Event metadata
    event_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="Format: <domain>.<aggregate>.<action>",
    )
    event_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    producer_module: Mapped[str] = mapped_column(String(50), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(100), nullable=False)

    # Correlation
    correlation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    causation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("platform.event_outbox.event_id"),
        nullable=True,
    )

    # Idempotency
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)

    # Payload
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    headers: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        comment="Required: organization_id, user_id, request_id, ip_address, source",
    )

    # Processing state
    status: Mapped[EventStatus] = mapped_column(
        Enum(EventStatus, name="event_status"),
        nullable=False,
        default=EventStatus.PENDING,
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_class: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        comment="Exception class name of the most recent delivery failure",
    )
    terminal_reason: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment="Why the event reached a terminal status (see TerminalReason)",
    )

    # Claim / lease state (claim-deliver-settle relay protocol).
    # A PENDING/FAILED row with an unexpired lease is claimed and invisible
    # to other claimants; settlement requires the matching claim_token.
    claimed_by: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        comment="Worker identity (host:pid) that last claimed this event",
    )
    claim_token: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Token stamped at claim time; settlement must present it",
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Claim lease expiry; expired leases are reclaimable",
    )

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    caused_by: Mapped[Optional["EventOutbox"]] = relationship(
        "EventOutbox",
        remote_side=[event_id],
        foreign_keys=[causation_id],
    )
