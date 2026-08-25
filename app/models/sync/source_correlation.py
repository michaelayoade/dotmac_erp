"""Source-qualified correlations for rebuildable cross-application context."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SourceEntityType(str, enum.Enum):
    """Type of source fact correlated to an ERP projection."""

    PROJECT = "PROJECT"
    TICKET = "TICKET"
    WORK_ORDER = "WORK_ORDER"
    MATERIAL_REQUEST = "MATERIAL_REQUEST"
    PURCHASE_ORDER = "PURCHASE_ORDER"


class SourceCorrelationStatus(str, enum.Enum):
    """Lifecycle of a source correlation, not of the local domain row."""

    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    ARCHIVED = "ARCHIVED"


class SourceCorrelation(Base):
    """Map a source-qualified observation to its rebuildable ERP row."""

    __tablename__ = "source_correlation"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "source_application",
            "source_entity_type",
            "source_reference",
            name="uq_source_correlation_org_app_type_ref",
        ),
        Index("idx_source_correlation_org", "organization_id"),
        Index("idx_source_correlation_source_reference", "source_reference"),
        Index("idx_source_correlation_local", "local_entity_type", "local_entity_id"),
        Index(
            "idx_source_correlation_status", "organization_id", "source_entity_type", "source_status"
        ),
        {"schema": "sync"},
    )

    # Primary key
    mapping_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # Organization (multi-tenancy)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
    )

    source_application: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Owning source application; legacy_unknown is never inferred",
    )
    source_entity_type: Mapped[SourceEntityType] = mapped_column(
        Enum(SourceEntityType, name="source_entity_type", schema="sync"),
        nullable=False,
    )
    source_reference: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        comment="Opaque identifier inside source_application",
    )

    # Local ERP entity reference
    local_entity_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Target table: 'project', 'ticket', 'task'",
    )
    local_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="UUID of the entity in ERP",
    )

    # Projection correlation status; never treated as the local lifecycle.
    source_status: Mapped[SourceCorrelationStatus] = mapped_column(
        Enum(
            SourceCorrelationStatus,
            name="source_correlation_status",
            schema="sync",
        ),
        nullable=False,
        default=SourceCorrelationStatus.ACTIVE,
    )

    # Rebuildable display facts from the source observation.
    display_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Name/subject/title from the source for display",
    )
    display_code: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
        comment="Code/number from the source",
    )
    customer_name: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        comment="Customer name from the source",
    )

    # Rebuildable source observation used for local context.
    source_payload: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment="Source observation payload for repair and reconciliation",
    )

    # Sync tracking
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Last update time reported by the source",
    )

    # Error tracking
    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Audit
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"<SourceCorrelation({self.source_application}:"
            f"{self.source_entity_type.value}:"
            f"{self.source_reference} -> {self.local_entity_type}:{self.local_entity_id})>"
        )
