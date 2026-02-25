"""
Balance Refresh Queue Model - GL schema.

Tracks balances that must be recalculated from posted ledger lines.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class BalanceRefreshQueue(Base):
    """Queue of account/period combinations pending balance refresh."""

    __tablename__ = "balance_refresh_queue"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "account_id",
            "fiscal_period_id",
            name="uq_balance_refresh_key",
        ),
        Index("ix_balance_refresh_pending", "processed_at", "invalidated_at"),
        Index("ix_balance_refresh_org", "organization_id", "invalidated_at"),
        {"schema": "gl"},
    )

    queue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    invalidated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
