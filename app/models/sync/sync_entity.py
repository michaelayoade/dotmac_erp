"""
Sync Entity Model - Track individual entity sync status.

Maps external system records (ERPNext) to DotMac ERP entities.
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
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SyncStatus(str, enum.Enum):
    """Status of entity sync."""

    PENDING = "PENDING"
    SYNCED = "SYNCED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class SyncEntity(Base):
    """
    Track sync state for individual entities.

    Maps external system records to DotMac ERP entities, enabling:
    - Incremental syncs (only new/modified records)
    - Audit trail of imports
    - Error tracking and retry
    """

    __tablename__ = "sync_entity"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "source_system",
            "source_doctype",
            "source_name",
            name="uq_sync_entity_source",
        ),
        Index("idx_sync_entity_org", "organization_id"),
        Index("idx_sync_entity_status", "sync_status"),
        Index("idx_sync_entity_target", "target_table", "target_id"),
        {"schema": "sync"},
    )

    sync_id: Mapped[uuid.UUID] = mapped_column(
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

    # Source system identification
    source_system: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # e.g., 'erpnext'
    source_doctype: Mapped[str] = mapped_column(
        String(100), nullable=False
    )  # e.g., 'Item', 'Asset'
    source_name: Mapped[str] = mapped_column(
        String(255), nullable=False
    )  # ERPNext document name

    # Target entity in DotMac ERP
    target_table: Mapped[str] = mapped_column(
        String(100), nullable=False
    )  # e.g., 'inv.item', 'fa.asset'
    target_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # DotMac ERP entity ID (null if failed)

    # Sync status
    sync_status: Mapped[SyncStatus] = mapped_column(
        Enum(SyncStatus, name="sync_status"),
        nullable=False,
        default=SyncStatus.PENDING,
    )

    # Timestamps for incremental sync
    source_modified: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # ERPNext modified timestamp
    synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Error tracking
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        onupdate=func.now(),
        nullable=True,
    )

    def mark_synced(self, target_id: uuid.UUID) -> None:
        """Mark entity as successfully synced."""
        self.target_id = target_id
        self.sync_status = SyncStatus.SYNCED
        self.synced_at = datetime.now()
        self.error_message = None

    def mark_failed(self, error: str) -> None:
        """Mark entity as failed with error message."""
        self.sync_status = SyncStatus.FAILED
        self.error_message = error
        self.retry_count = (self.retry_count or 0) + 1

    def mark_skipped(self, reason: str) -> None:
        """Mark entity as skipped (e.g., already exists, disabled)."""
        self.sync_status = SyncStatus.SKIPPED
        self.error_message = reason
