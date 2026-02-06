"""
Case Document Model - HR Schema.

Stores documents and evidence related to disciplinary cases.
"""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.people.hr.employee import Employee
    from app.models.people.discipline.case import DisciplinaryCase


class DocumentType(str, enum.Enum):
    """Types of case documents."""

    EVIDENCE = "EVIDENCE"  # Supporting evidence
    QUERY_LETTER = "QUERY_LETTER"  # Formal query letter
    EMPLOYEE_RESPONSE = "EMPLOYEE_RESPONSE"  # Employee's written response
    WITNESS_STATEMENT = "WITNESS_STATEMENT"  # Witness statements
    HEARING_MINUTES = "HEARING_MINUTES"  # Minutes from hearing
    DECISION_LETTER = "DECISION_LETTER"  # Formal decision notification
    APPEAL_LETTER = "APPEAL_LETTER"  # Employee's appeal
    APPEAL_DECISION = "APPEAL_DECISION"  # Appeal outcome letter
    WARNING_LETTER = "WARNING_LETTER"  # Written warning document
    TERMINATION_LETTER = "TERMINATION_LETTER"  # Termination notice
    OTHER = "OTHER"  # Other documents


class CaseDocument(Base):
    """
    Case Document Model.

    Stores metadata for documents attached to disciplinary cases.
    Actual files are stored in object storage; this tracks metadata.
    """

    __tablename__ = "case_document"
    __table_args__ = {"schema": "hr"}

    # Primary key
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # Parent case
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.disciplinary_case.case_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Document details
    document_type: Mapped[DocumentType] = mapped_column(
        Enum(DocumentType, name="document_type", schema="hr"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Document title",
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Document description",
    )

    # File storage
    file_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="Path to file in object storage",
    )
    file_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Original file name",
    )
    file_size: Mapped[Optional[int]] = mapped_column(
        nullable=True,
        comment="File size in bytes",
    )
    mime_type: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="MIME type of file",
    )

    # Uploaded by
    uploaded_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hr.employee.employee_id"),
        nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    case: Mapped["DisciplinaryCase"] = relationship(
        "DisciplinaryCase",
        back_populates="documents",
    )
    uploaded_by: Mapped[Optional["Employee"]] = relationship("Employee")

    def __repr__(self) -> str:
        return f"<CaseDocument {self.document_id} - {self.title}>"
