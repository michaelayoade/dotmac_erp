"""
Posting Batch Model - GL Schema.
Document 07: Batch posting with idempotency.
"""
import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class BatchStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    POSTED = "POSTED"
    FAILED = "FAILED"
    PARTIALLY_POSTED = "PARTIALLY_POSTED"


class PostingBatch(Base):
    """
    Posting batch for grouping journal postings.
    Document 07: Idempotent posting with correlation.
    """

    __tablename__ = "posting_batch"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_batch_idempotency"),
        Index("idx_batch_status", "organization_id", "status"),
        Index("idx_batch_correlation", "correlation_id"),
        {"schema": "gl"},
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(
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
    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.fiscal_period.fiscal_period_id"),
        nullable=False,
    )

    # Idempotency (Document 07)
    idempotency_key: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="Format: <org_id>:<source_module>:<source_id>:<version>",
    )

    # Source
    source_module: Mapped[str] = mapped_column(String(20), nullable=False)
    batch_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Stats
    total_entries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    posted_entries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_entries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Status
    status: Mapped[BatchStatus] = mapped_column(
        Enum(BatchStatus, name="batch_status"),
        nullable=False,
        default=BatchStatus.PENDING,
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Lifecycle
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    submitted_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    processing_started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    correlation_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
