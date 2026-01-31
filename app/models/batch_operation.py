"""
Batch Operation Tracking Model.

Tracks script runs, bulk imports, and other batch operations for audit purposes.
Each batch operation gets a unique ID that can be stored on created records.
"""

import enum
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class BatchOperationType(str, enum.Enum):
    """Type of batch operation."""
    SCRIPT = "script"           # Manual script run (e.g., seed_payroll_from_excel.py)
    IMPORT = "import"           # Data import (CSV, Excel)
    SYNC = "sync"               # External system sync (ERPNext)
    MIGRATION = "migration"     # Data migration
    BULK_UPDATE = "bulk_update" # Bulk update operation
    CLEANUP = "cleanup"         # Data cleanup/deletion


class BatchOperationStatus(str, enum.Enum):
    """Status of batch operation."""
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class BatchOperation(Base):
    """
    Tracks batch operations (scripts, imports, syncs) for audit trail.

    Usage in scripts:
        batch = BatchOperation(
            organization_id=org_id,
            operation_type=BatchOperationType.SCRIPT,
            operation_name="seed_payroll_from_excel",
            started_by_id=user_id,
        )
        db.add(batch)
        db.flush()

        # Then on each created record:
        employee = Employee(..., batch_operation_id=batch.id)

        # At end:
        batch.mark_completed(created=50, updated=10, skipped=5)
    """
    __tablename__ = "batch_operations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Multi-tenant
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Operation details
    # Use existing PostgreSQL enum type (create_type=False means don't try to create it)
    operation_type: Mapped[BatchOperationType] = mapped_column(
        Enum(
            BatchOperationType,
            name="batch_operation_type",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    operation_name: Mapped[str] = mapped_column(
        String(120), nullable=False
    )  # e.g., "seed_payroll_from_excel", "import_employees"

    description: Mapped[Optional[str]] = mapped_column(Text)

    # Source file info (for imports)
    source_file: Mapped[Optional[str]] = mapped_column(String(512))
    source_checksum: Mapped[Optional[str]] = mapped_column(String(64))  # SHA256 of file

    # Who ran it
    started_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Timing
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Status - use existing PostgreSQL enum type
    status: Mapped[BatchOperationStatus] = mapped_column(
        Enum(
            BatchOperationStatus,
            name="batch_operation_status",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        default=BatchOperationStatus.RUNNING,
    )

    # Statistics
    records_created: Mapped[int] = mapped_column(Integer, default=0)
    records_updated: Mapped[int] = mapped_column(Integer, default=0)
    records_skipped: Mapped[int] = mapped_column(Integer, default=0)
    records_failed: Mapped[int] = mapped_column(Integer, default=0)

    # Error info
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    # Detailed log of what was created (for rollback)
    # Format: {"people": ["uuid1", "uuid2"], "employees": ["uuid3"]}
    created_entity_ids: Mapped[Optional[dict]] = mapped_column(
        MutableDict.as_mutable(JSON)
    )

    # Additional metadata
    metadata_: Mapped[Optional[dict]] = mapped_column(
        "metadata", MutableDict.as_mutable(JSON)
    )

    def mark_completed(
        self,
        created: int = 0,
        updated: int = 0,
        skipped: int = 0,
        failed: int = 0,
    ) -> None:
        """Mark the operation as completed with statistics."""
        self.status = BatchOperationStatus.COMPLETED
        self.completed_at = datetime.now(timezone.utc)
        self.records_created = created
        self.records_updated = updated
        self.records_skipped = skipped
        self.records_failed = failed

    def mark_failed(self, error: str) -> None:
        """Mark the operation as failed with error message."""
        self.status = BatchOperationStatus.FAILED
        self.completed_at = datetime.now(timezone.utc)
        self.error_message = error

    def track_created(self, entity_type: str, entity_id: uuid.UUID) -> None:
        """Track a created entity for potential rollback."""
        if self.created_entity_ids is None:
            self.created_entity_ids = {}
        if entity_type not in self.created_entity_ids:
            self.created_entity_ids[entity_type] = []
        self.created_entity_ids[entity_type].append(str(entity_id))
