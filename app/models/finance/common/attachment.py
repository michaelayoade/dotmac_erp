"""
Attachment Model - Common Schema.

Generic attachment storage for any document type across IFRS modules.
"""

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AttachmentCategory(str, enum.Enum):
    """Categories for document attachments."""
    INVOICE = "INVOICE"
    RECEIPT = "RECEIPT"
    CONTRACT = "CONTRACT"
    PURCHASE_ORDER = "PURCHASE_ORDER"
    GOODS_RECEIPT = "GOODS_RECEIPT"
    PAYMENT = "PAYMENT"
    QUOTE = "QUOTE"
    CREDIT_NOTE = "CREDIT_NOTE"
    EXPENSE = "EXPENSE"
    JOURNAL = "JOURNAL"
    BANK_STATEMENT = "BANK_STATEMENT"
    TAX_DOCUMENT = "TAX_DOCUMENT"
    SUPPLIER = "SUPPLIER"
    CUSTOMER = "CUSTOMER"
    OTHER = "OTHER"


class Attachment(Base):
    """
    Document attachment for any entity.

    Supports polymorphic association via entity_type + entity_id.
    Files are stored externally (filesystem/S3), this model stores metadata.
    """

    __tablename__ = "attachment"
    __table_args__ = (
        Index("idx_attachment_entity", "organization_id", "entity_type", "entity_id"),
        Index("idx_attachment_category", "organization_id", "category"),
        {"schema": "common"},
    )

    attachment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    # Polymorphic association - links to any entity
    entity_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Type of entity: SUPPLIER_INVOICE, PURCHASE_ORDER, GOODS_RECEIPT, etc.",
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="ID of the related entity",
    )

    # File metadata
    file_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Original file name",
    )
    file_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="Storage path (relative path or S3 key)",
    )
    file_size: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment="File size in bytes",
    )
    content_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="MIME type",
    )

    # Classification
    category: Mapped[AttachmentCategory] = mapped_column(
        Enum(AttachmentCategory, name="attachment_category"),
        nullable=False,
        default=AttachmentCategory.OTHER,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="User-provided description",
    )

    # Storage info
    storage_provider: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="LOCAL",
        comment="Storage backend: LOCAL, S3, AZURE_BLOB, GCS",
    )
    checksum: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        comment="SHA-256 hash for integrity verification",
    )

    # Audit
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"<Attachment {self.file_name} ({self.entity_type}:{self.entity_id})>"
