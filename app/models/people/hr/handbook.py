"""
HR Document / Handbook Models.

Models for managing HR policy documents, handbooks, and employee acknowledgments.
Supports versioning, effective dates, and acknowledgment tracking.
"""

import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.finance.core_org import Organization
    from app.models.people.hr import Employee


class DocumentCategory(str, enum.Enum):
    """Categories for HR documents."""

    HANDBOOK = "HANDBOOK"  # Employee handbook
    POLICY = "POLICY"  # Company policies
    CODE_OF_CONDUCT = "CODE_OF_CONDUCT"
    SAFETY = "SAFETY"  # Health & safety
    BENEFITS = "BENEFITS"  # Benefits information
    IT_SECURITY = "IT_SECURITY"  # IT/security policies
    COMPLIANCE = "COMPLIANCE"  # Regulatory compliance
    TRAINING = "TRAINING"  # Training materials
    OTHER = "OTHER"


class DocumentStatus(str, enum.Enum):
    """Status of an HR document."""

    DRAFT = "DRAFT"  # Being prepared
    ACTIVE = "ACTIVE"  # Current and in effect
    SUPERSEDED = "SUPERSEDED"  # Replaced by newer version
    ARCHIVED = "ARCHIVED"  # No longer in use


class HRDocument(Base):
    """
    HR Document / Policy document.

    Stores handbook documents, policies, and other HR materials that
    employees may need to acknowledge. Supports versioning and
    effective date management.
    """

    __tablename__ = "hr_document"
    __table_args__ = (
        Index("idx_hr_document_org", "organization_id"),
        Index("idx_hr_document_category", "category"),
        Index("idx_hr_document_status", "status"),
        UniqueConstraint(
            "organization_id",
            "document_code",
            "version",
            name="uq_hr_document_code_version",
        ),
        {"schema": "hr"},
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

    # Document identification
    document_code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Unique code for this document, e.g., HB-001, POL-IT-001",
    )
    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="Document title",
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Brief description of the document",
    )

    # Classification
    category: Mapped[DocumentCategory] = mapped_column(
        Enum(DocumentCategory, name="hr_document_category"),
        nullable=False,
        default=DocumentCategory.POLICY,
    )

    # Versioning
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment="Document version number",
    )
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.hr_document.document_id"),
        nullable=True,
        comment="Reference to previous version",
    )

    # File storage
    file_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="Storage path for the document file",
    )
    file_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Original filename",
    )
    content_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="application/pdf",
        comment="MIME type",
    )
    file_size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
    )
    content_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="SHA256 hash for integrity verification",
    )

    # Dates
    effective_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        default=date.today,
        comment="Date this version becomes effective",
    )
    expiry_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Optional expiry date",
    )

    # Acknowledgment requirements
    requires_acknowledgment: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="Whether employees must acknowledge this document",
    )
    acknowledgment_deadline_days: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Days from onboarding/effective date to acknowledge",
    )
    applies_to_all_employees: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="If false, specific departments/roles may be defined",
    )
    applies_to_departments: Mapped[list[str] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="List of department IDs this applies to (if not all)",
    )

    # Status
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="hr_document_status"),
        nullable=False,
        default=DocumentStatus.DRAFT,
    )

    # Metadata
    tags: Mapped[list[str] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Tags for searching/filtering",
    )
    extra_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
        comment="Additional metadata",
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
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        foreign_keys=[organization_id],
        lazy="joined",
    )
    previous_version: Mapped[Optional["HRDocument"]] = relationship(
        "HRDocument",
        remote_side=[document_id],
        foreign_keys=[previous_version_id],
        lazy="select",
    )
    acknowledgments: Mapped[list["HRDocumentAcknowledgment"]] = relationship(
        "HRDocumentAcknowledgment",
        back_populates="document",
        lazy="dynamic",
    )

    def __repr__(self) -> str:
        return f"<HRDocument({self.document_code} v{self.version}: {self.title})>"

    @property
    def is_active(self) -> bool:
        """Check if document is currently active."""
        if self.status != DocumentStatus.ACTIVE:
            return False
        today = date.today()
        if self.effective_date > today:
            return False
        return not (self.expiry_date and self.expiry_date < today)

    @property
    def file_size_display(self) -> str:
        """Human-readable file size."""
        size = self.file_size_bytes
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"


class HRDocumentAcknowledgment(Base):
    """
    Employee acknowledgment of an HR document.

    Tracks when employees have read and acknowledged HR documents/policies.
    Used for compliance tracking and audit purposes.
    """

    __tablename__ = "hr_document_acknowledgment"
    __table_args__ = (
        Index("idx_hr_doc_ack_document", "document_id"),
        Index("idx_hr_doc_ack_employee", "employee_id"),
        UniqueConstraint(
            "document_id", "employee_id", name="uq_hr_doc_ack_document_employee"
        ),
        {"schema": "hr"},
    )

    acknowledgment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.hr_document.document_id"),
        nullable=False,
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=False,
    )

    # Acknowledgment details
    acknowledged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    ip_address: Mapped[str | None] = mapped_column(
        String(45),
        nullable=True,
        comment="IP address at time of acknowledgment",
    )
    user_agent: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="Browser user agent for audit",
    )

    # Optional digital signature/confirmation
    signature_data: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Base64 encoded signature image if captured",
    )
    confirmation_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Text the employee confirmed, e.g., 'I have read and understood...'",
    )

    # Relationships
    document: Mapped["HRDocument"] = relationship(
        "HRDocument",
        back_populates="acknowledgments",
        lazy="joined",
    )
    employee: Mapped["Employee"] = relationship(
        "Employee",
        foreign_keys=[employee_id],
        lazy="joined",
    )

    def __repr__(self) -> str:
        return f"<HRDocumentAcknowledgment(doc={self.document_id}, emp={self.employee_id})>"
