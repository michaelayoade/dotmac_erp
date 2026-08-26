"""
Event Handler Receipt - per (event, handler) at-most-once execution receipt.
Document 10: Event-Driven Architecture.

This is LOCAL at-most-once machinery, not a position in an external feed.
``event_id`` is a foreign key into ERP's own ``platform.event_outbox``; the
relay (``app/tasks/outbox_relay.py``) claims, delivers and settles those rows
in-process, and nothing here records progress over any upstream stream. The
row is a receipt: "handler H has been run for local event E, with this
outcome". Renamed from ``EventHandlerCheckpoint`` after the Governance ADR 0010
adjudication recorded in ``docs/inventories/external-connector-surface.md``.

PERSISTENCE IDENTITY IS UNCHANGED BY THAT RENAME, deliberately: the table is
still ``platform.event_handler_checkpoint``, the primary key column is still
``checkpoint_id``, the unique constraint is still
``uq_checkpoint_event_handler``, and the PostgreSQL enum type is still
``checkpoint_status``. The migration lineage
(``alembic/versions/create_ifrs_schemas.py``) is untouched. Changing any of
those names would be a migration wearing a rename's clothes; the frozen set is
enforced by ``scripts/check_connector_adoption.py --persistence``.
"""

import enum
import uuid
from datetime import datetime

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


class HandlerReceiptStatus(str, enum.Enum):
    # The member values and the PostgreSQL type name (``checkpoint_status``,
    # declared on the column below) are persistence identity and stay as
    # created.
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class EventHandlerReceipt(Base):
    """
    Records that one handler has processed one LOCAL outbox event.
    Ensures each handler applies an event's consequence at most once.
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

    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    status: Mapped[HandlerReceiptStatus] = mapped_column(
        Enum(HandlerReceiptStatus, name="checkpoint_status"),
        nullable=False,
        default=HandlerReceiptStatus.PENDING,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

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
