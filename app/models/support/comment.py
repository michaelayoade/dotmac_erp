"""
Ticket Comment Model.

Represents comments, notes, and activity log entries on support tickets.
"""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    Boolean,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.support.ticket import Ticket
    from app.models.support.attachment import TicketAttachment
    from app.models.person import Person


class CommentType(str, enum.Enum):
    """Type of comment entry."""

    COMMENT = "COMMENT"  # Public comment visible to all
    INTERNAL_NOTE = "INTERNAL_NOTE"  # Internal note for staff only
    SYSTEM = "SYSTEM"  # System-generated activity log


class TicketComment(Base):
    """
    Comment or activity entry on a ticket.

    Supports:
    - Public comments
    - Internal notes (staff only)
    - System-generated activity log (status changes, assignments, etc.)
    """

    __tablename__ = "ticket_comment"
    __table_args__ = {"schema": "support"}

    # Primary key
    comment_id: Mapped[uuid.UUID] = mapped_column(
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

    # Comment type
    comment_type: Mapped[CommentType] = mapped_column(
        Enum(CommentType, name="comment_type", schema="support"),
        nullable=False,
        default=CommentType.COMMENT,
    )

    # Content
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Comment content (supports markdown)",
    )

    # For system comments - what action occurred
    action: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="Action type for system comments (status_change, assigned, etc.)",
    )

    # For system comments - old and new values
    old_value: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Previous value for change tracking",
    )
    new_value: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="New value for change tracking",
    )

    # Author
    author_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.people.id"),  # Cross-schema FK needs full path
        nullable=True,
        comment="Person who created the comment (null for system)",
    )

    # Visibility
    is_internal: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="If true, only visible to staff",
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
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    ticket: Mapped["Ticket"] = relationship(
        "Ticket",
        back_populates="comments",
    )
    author: Mapped[Optional["Person"]] = relationship(
        "Person",
        primaryjoin="TicketComment.author_id == Person.id",
        foreign_keys="TicketComment.author_id",
        lazy="joined",
        viewonly=True,
    )
    attachments: Mapped[list["TicketAttachment"]] = relationship(
        "TicketAttachment",
        back_populates="comment",
    )

    def __repr__(self) -> str:
        return f"<TicketComment(id={self.comment_id}, type={self.comment_type}, ticket={self.ticket_id})>"

    @classmethod
    def create_system_comment(
        cls,
        ticket_id: uuid.UUID,
        action: str,
        content: str,
        old_value: Optional[str] = None,
        new_value: Optional[str] = None,
        author_id: Optional[uuid.UUID] = None,
    ) -> "TicketComment":
        """Create a system-generated activity log entry."""
        return cls(
            ticket_id=ticket_id,
            comment_type=CommentType.SYSTEM,
            action=action,
            content=content,
            old_value=old_value,
            new_value=new_value,
            author_id=author_id,
            is_internal=True,
        )
