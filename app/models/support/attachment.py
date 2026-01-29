"""
Ticket Attachment Model.

Represents file attachments on support tickets.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    String,
    Boolean,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.support.ticket import Ticket
    from app.models.support.comment import TicketComment
    from app.models.person import Person


class TicketAttachment(Base):
    """
    File attachment on a ticket.

    Supports images, documents, and other files.
    Files are stored in object storage with metadata in database.
    """

    __tablename__ = "ticket_attachment"
    __table_args__ = {"schema": "support"}

    # Primary key
    attachment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    # Foreign key to ticket
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("support.ticket.ticket_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Optional link to comment (if attached via comment)
    comment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("support.ticket_comment.comment_id", ondelete="SET NULL"),
        nullable=True,
    )

    # File metadata
    filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Original filename",
    )
    storage_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="Path in storage (local or S3)",
    )
    content_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="MIME type",
    )
    file_size: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment="File size in bytes",
    )

    # Thumbnail for images
    thumbnail_path: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="Path to thumbnail (for images)",
    )

    # Uploader
    uploaded_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
    )

    # Soft delete
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    ticket: Mapped["Ticket"] = relationship(
        "Ticket",
        back_populates="attachments",
    )
    comment: Mapped[Optional["TicketComment"]] = relationship(
        "TicketComment",
        back_populates="attachments",
    )
    uploaded_by: Mapped[Optional["Person"]] = relationship(
        "Person",
        primaryjoin="TicketAttachment.uploaded_by_id == Person.id",
        foreign_keys="TicketAttachment.uploaded_by_id",
        lazy="joined",
        viewonly=True,
    )

    def __repr__(self) -> str:
        return f"<TicketAttachment(id={self.attachment_id}, filename={self.filename})>"

    @property
    def is_image(self) -> bool:
        """Check if attachment is an image."""
        return self.content_type.startswith("image/")

    @property
    def file_size_display(self) -> str:
        """Human-readable file size."""
        size = self.file_size
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"
