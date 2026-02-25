"""Service hook registration model."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.finance.platform.service_hook_execution import ServiceHookExecution


class HookHandlerType(str, enum.Enum):
    """Supported hook handler types."""

    NOTIFICATION = "NOTIFICATION"
    WEBHOOK = "WEBHOOK"
    EMAIL = "EMAIL"
    INTERNAL_SERVICE = "INTERNAL_SERVICE"
    EVENT_OUTBOX = "EVENT_OUTBOX"


class HookExecutionMode(str, enum.Enum):
    """Hook execution strategy."""

    SYNC = "SYNC"
    ASYNC = "ASYNC"


class ServiceHook(Base):
    """A registered hook that fires when matching event is emitted."""

    __tablename__ = "service_hook"
    __table_args__ = (
        Index("ix_hook_event_org", "event_name", "organization_id"),
        Index("ix_hook_active", "is_active", "event_name"),
        {"schema": "platform"},
    )

    hook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id", ondelete="CASCADE"),
        nullable=True,
    )

    event_name: Mapped[str] = mapped_column(String(100), nullable=False)
    handler_type: Mapped[HookHandlerType] = mapped_column(
        Enum(HookHandlerType, name="hook_handler_type"),
        nullable=False,
    )
    execution_mode: Mapped[HookExecutionMode] = mapped_column(
        Enum(HookExecutionMode, name="hook_execution_mode"),
        nullable=False,
        default=HookExecutionMode.ASYNC,
        server_default=text("'ASYNC'"),
    )

    handler_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    conditions: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=10,
        server_default=text("10"),
    )

    max_retries: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
        server_default=text("3"),
    )
    retry_backoff_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=60,
        server_default=text("60"),
    )

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    executions: Mapped[list[ServiceHookExecution]] = relationship(
        "ServiceHookExecution",
        back_populates="hook",
        cascade="all, delete-orphan",
        lazy="noload",
    )
