"""
External Sync Tracking - AR Schema.

Tracks mappings between external system IDs and ERP entity IDs.
"""

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, Index, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ExternalSource(str, enum.Enum):
    """External data sources."""

    SPLYNX = "SPLYNX"
    ERPNEXT = "ERPNEXT"
    CRM = "CRM"


class EntityType(str, enum.Enum):
    """Entity types that can be synced."""

    CUSTOMER = "CUSTOMER"
    INVOICE = "INVOICE"
    PAYMENT = "PAYMENT"
    CREDIT_NOTE = "CREDIT_NOTE"


class ExternalSync(Base):
    """
    Tracks mapping between external system IDs and ERP entity IDs.

    This enables:
    - Incremental sync (only fetch new/changed records)
    - Deduplication (avoid creating duplicates)
    - Audit trail (when was each record synced)
    """

    __tablename__ = "external_sync"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "source",
            "entity_type",
            "external_id",
            name="uq_external_sync_source_entity",
        ),
        Index(
            "idx_external_sync_lookup",
            "organization_id",
            "source",
            "entity_type",
            "external_id",
        ),
        Index("idx_external_sync_local", "organization_id", "local_entity_id"),
        {"schema": "ar"},
    )

    sync_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    # External system info
    source: Mapped[ExternalSource] = mapped_column(
        Enum(ExternalSource, name="external_source"),
        nullable=False,
    )
    entity_type: Mapped[EntityType] = mapped_column(
        Enum(EntityType, name="sync_entity_type"),
        nullable=False,
    )
    external_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="ID in the external system (e.g., Splynx customer ID)",
    )

    # Local ERP entity
    local_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="UUID of the entity in ERP (customer_id, invoice_id, etc.)",
    )

    # Sync metadata
    external_updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Last update time in external system (for incremental sync)",
    )
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="When this record was last synced",
    )
    sync_hash: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        comment="Hash of synced data (for change detection)",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
