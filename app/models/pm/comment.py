"""
PM Comment Model.

Represents comments and activity entries on projects and tasks.
"""
import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.person import Person
    from app.models.finance.common.attachment import Attachment


class PMCommentType(str, enum.Enum):
    """Type of PM comment entry."""

    COMMENT = "COMMENT"
    INTERNAL_NOTE = "INTERNAL_NOTE"
    SYSTEM = "SYSTEM"


class PMComment(Base):
    """
    Comment or activity entry on a project or task.

    Uses polymorphic association via entity_type + entity_id.
    """

    __tablename__ = "pm_comment"
    __table_args__ = {"schema": "pm"}

    comment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )

    entity_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="PROJECT or TASK",
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        comment="ID of the related project or task",
    )

    comment_type: Mapped[PMCommentType] = mapped_column(
        Enum(PMCommentType, name="pm_comment_type", schema="pm"),
        nullable=False,
        default=PMCommentType.COMMENT,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Comment content (supports markdown)",
    )
    action: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="Action type for system comments",
    )
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

    author_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("people.id"),
        nullable=True,
        comment="Person who created the comment (null for system)",
    )

    is_internal: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

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

    author: Mapped[Optional["Person"]] = relationship(
        "Person",
        primaryjoin="PMComment.author_id == Person.id",
        foreign_keys="PMComment.author_id",
        lazy="joined",
        viewonly=True,
    )
    attachments: Mapped[list["PMCommentAttachment"]] = relationship(
        "PMCommentAttachment",
        back_populates="comment",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<PMComment(id={self.comment_id}, type={self.comment_type}, entity={self.entity_type})>"


class PMCommentAttachment(Base):
    """Links PM comments to common attachments."""

    __tablename__ = "pm_comment_attachment"
    __table_args__ = {"schema": "pm"}

    comment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pm.pm_comment.comment_id", ondelete="CASCADE"),
        primary_key=True,
    )
    attachment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("common.attachment.attachment_id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    comment: Mapped["PMComment"] = relationship(
        "PMComment",
        back_populates="attachments",
    )
    attachment: Mapped["Attachment"] = relationship(
        "Attachment",
        lazy="joined",
    )
