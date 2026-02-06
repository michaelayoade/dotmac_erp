"""
Generated Document Model.

Tracks documents generated from templates for audit trail and retrieval.
"""

import enum
import uuid
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class DocumentStatus(str, enum.Enum):
    """Status of a generated document."""

    DRAFT = "DRAFT"  # Document generated but not finalized
    FINAL = "FINAL"  # Document finalized
    SENT = "SENT"  # Email sent or document delivered
    SUPERSEDED = "SUPERSEDED"  # Replaced by newer version
    VOIDED = "VOIDED"  # Cancelled/voided


class OutputFormat(str, enum.Enum):
    """Output format of the generated document."""

    PDF = "PDF"
    HTML = "HTML"
    EMAIL = "EMAIL"


class GeneratedDocument(Base):
    """
    Record of a generated document instance.

    Tracks when documents were generated, for whom, and stores
    the rendered output reference. Provides audit trail for all
    generated documents across the system.
    """

    __tablename__ = "generated_document"
    __table_args__ = (
        Index("idx_generated_doc_org", "organization_id"),
        Index("idx_generated_doc_entity", "entity_type", "entity_id"),
        Index("idx_generated_doc_template", "template_id"),
        Index("idx_generated_doc_number", "organization_id", "document_number"),
        {"schema": "automation"},
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
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

    # Template reference
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("automation.document_template.template_id"),
        nullable=False,
    )
    template_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Template version at time of generation",
    )

    # Entity this document is for (polymorphic)
    entity_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Type: JOB_OFFER, EMPLOYEE, INVOICE, etc.",
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="ID of the related entity",
    )

    # Document metadata
    document_number: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="Document reference number, e.g., OFFER-2024-0001",
    )
    document_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        default=date.today,
    )
    document_title: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
        comment="Human-readable document title",
    )

    # Output details
    output_format: Mapped[OutputFormat] = mapped_column(
        Enum(OutputFormat, name="generated_doc_output_format"),
        nullable=False,
    )
    file_path: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="Storage path for file-based output",
    )
    file_size_bytes: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )
    content_hash: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        comment="SHA256 hash for integrity verification",
    )

    # Email-specific fields
    sent_to: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Recipient email address",
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Context snapshot for audit/debugging
    context_snapshot: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
        comment="Key data values at generation time",
    )

    # Status tracking
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="generated_doc_status"),
        nullable=False,
        default=DocumentStatus.DRAFT,
    )
    superseded_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("automation.generated_document.document_id"),
        nullable=True,
        comment="If superseded, reference to newer version",
    )

    # Audit
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    template = relationship(
        "DocumentTemplate",
        foreign_keys=[template_id],
        lazy="joined",
    )
    superseding_document = relationship(
        "GeneratedDocument",
        remote_side=[document_id],
        foreign_keys=[superseded_by],
        lazy="select",
    )
