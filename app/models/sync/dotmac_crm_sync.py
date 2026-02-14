"""
DotMac CRM Sync Mapping Model - Track entities synced from DotMac CRM.

Maps CRM entity IDs to local ERP entities (Project, Ticket, Task).
"""

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


class CRMEntityType(str, enum.Enum):
    """Type of entity synced from DotMac CRM."""

    PROJECT = "PROJECT"
    TICKET = "TICKET"
    WORK_ORDER = "WORK_ORDER"
    MATERIAL_REQUEST = "MATERIAL_REQUEST"
    PURCHASE_ORDER = "PURCHASE_ORDER"


class CRMSyncStatus(str, enum.Enum):
    """Status of CRM sync mapping."""

    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    ARCHIVED = "ARCHIVED"


class CRMSyncMapping(Base):
    """
    Maps DotMac CRM entities to local ERP entities.

    Enables:
    - Tracking which CRM entities are synced
    - Looking up local entity from CRM ID
    - Storing CRM metadata for display without re-fetching
    """

    __tablename__ = "crm_sync_mapping"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "crm_entity_type",
            "crm_id",
            name="uq_crm_sync_org_type_id",
        ),
        Index("idx_crm_sync_org", "organization_id"),
        Index("idx_crm_sync_crm_id", "crm_id"),
        Index("idx_crm_sync_local", "local_entity_type", "local_entity_id"),
        Index(
            "idx_crm_sync_status", "organization_id", "crm_entity_type", "crm_status"
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

    # CRM source identification
    crm_entity_type: Mapped[CRMEntityType] = mapped_column(
        Enum(CRMEntityType, name="crm_entity_type", schema="sync"),
        nullable=False,
    )
    crm_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        comment="UUID from DotMac CRM",
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

    # CRM status tracking (mirrors CRM's status for filtering)
    crm_status: Mapped[CRMSyncStatus] = mapped_column(
        Enum(CRMSyncStatus, name="crm_sync_status", schema="sync"),
        nullable=False,
        default=CRMSyncStatus.ACTIVE,
    )

    # Cached display data from CRM (avoids re-fetching for dropdowns)
    display_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Name/subject/title from CRM for display",
    )
    display_code: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
        comment="Code/number from CRM",
    )
    customer_name: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        comment="Customer name from CRM",
    )

    # Full CRM data cache (for detailed views)
    crm_data: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment="Full payload from CRM for reference",
    )

    # Sync tracking
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    crm_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Last update time in CRM",
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
            f"<CRMSyncMapping({self.crm_entity_type.value}:"
            f"{self.crm_id} -> {self.local_entity_type}:{self.local_entity_id})>"
        )
