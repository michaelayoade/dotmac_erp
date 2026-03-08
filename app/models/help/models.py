"""
Help Center Models.

Database-backed models for:
- Admin-authored article overrides
- User progress (article completion)
- Article feedback (helpful / not helpful)
- Search analytics (query logging)
"""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ArticleStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class HelpArticleOverride(Base):
    """
    Admin-authored article content that overlays static articles by slug match.

    When a DB override exists for a slug, it wins over the static dict content.
    """

    __tablename__ = "help_article_override"
    __table_args__ = (
        Index("idx_help_article_org_slug", "organization_id", "slug", unique=True),
        Index("idx_help_article_org_status", "organization_id", "status"),
        Index("idx_help_article_module", "organization_id", "module_key"),
    )

    article_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    module_key: Mapped[str] = mapped_column(String(50), nullable=False)
    content_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="workflow"
    )
    status: Mapped[ArticleStatus] = mapped_column(
        Enum(ArticleStatus, name="help_article_status"),
        nullable=False,
        default=ArticleStatus.DRAFT,
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class HelpUserProgress(Base):
    """
    Tracks article completion per user.

    One row per (person_id, article_slug, organization_id).
    """

    __tablename__ = "help_user_progress"
    __table_args__ = (
        Index(
            "idx_help_progress_unique",
            "organization_id",
            "person_id",
            "article_slug",
            unique=True,
        ),
        Index("idx_help_progress_person", "person_id"),
    )

    progress_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=False
    )
    article_slug: Mapped[str] = mapped_column(String(200), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class HelpArticleFeedback(Base):
    """
    Article feedback — helpful or not helpful, with optional comment.

    One row per (person_id, article_slug, organization_id).
    """

    __tablename__ = "help_article_feedback"
    __table_args__ = (
        Index(
            "idx_help_feedback_unique",
            "organization_id",
            "person_id",
            "article_slug",
            unique=True,
        ),
        Index("idx_help_feedback_slug", "organization_id", "article_slug"),
    )

    feedback_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=False
    )
    article_slug: Mapped[str] = mapped_column(String(200), nullable=False)
    rating: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # "helpful" or "not_helpful"
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class HelpSearchEvent(Base):
    """
    Search analytics — records each search query for insights.

    Used to identify popular searches and zero-result queries.
    """

    __tablename__ = "help_search_event"
    __table_args__ = (
        Index("idx_help_search_org", "organization_id"),
        Index("idx_help_search_created", "created_at"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=True
    )
    query: Mapped[str] = mapped_column(String(500), nullable=False)
    filters: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clicked_slug: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
