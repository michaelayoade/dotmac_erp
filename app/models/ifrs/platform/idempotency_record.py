"""
Idempotency Record - API idempotency tracking.
Document 11: API Design.
"""
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class IdempotencyRecord(Base):
    """
    API idempotency tracking.
    Stores responses for replay on duplicate requests.
    """

    __tablename__ = "idempotency_record"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "endpoint",
            "idempotency_key",
            name="uq_idempotency",
        ),
        Index("idx_idempotency_expires", "expires_at"),
        {"schema": "platform"},
    )

    record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
