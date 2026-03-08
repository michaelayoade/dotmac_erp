"""Help article feedback service.

Manages helpful / not-helpful feedback per user per article.
"""

import logging
from uuid import UUID

from sqlalchemy import func, select

from app.models.help.models import HelpArticleFeedback

logger = logging.getLogger(__name__)


class HelpFeedbackService:
    """Service for managing help article feedback."""

    def __init__(self, db):
        self.db = db

    def get_user_feedback(
        self, organization_id: UUID, person_id: UUID, slug: str
    ) -> str | None:
        """Return the user's current feedback rating for an article, or None."""
        stmt = select(HelpArticleFeedback.rating).where(
            HelpArticleFeedback.organization_id == organization_id,
            HelpArticleFeedback.person_id == person_id,
            HelpArticleFeedback.article_slug == slug,
        )
        return self.db.scalar(stmt)

    def get_user_feedback_map(
        self, organization_id: UUID, person_id: UUID
    ) -> dict[str, str]:
        """Return {slug: rating} for all articles the user has rated."""
        stmt = select(
            HelpArticleFeedback.article_slug, HelpArticleFeedback.rating
        ).where(
            HelpArticleFeedback.organization_id == organization_id,
            HelpArticleFeedback.person_id == person_id,
        )
        rows = self.db.execute(stmt).all()
        return {slug: rating for slug, rating in rows}

    def submit_feedback(
        self,
        organization_id: UUID,
        person_id: UUID,
        slug: str,
        rating: str,
        comment: str | None = None,
    ) -> str:
        """Submit or update feedback. Returns the saved rating."""
        if rating not in ("helpful", "not_helpful"):
            raise ValueError(f"Invalid rating: {rating}")

        existing = self.db.scalar(
            select(HelpArticleFeedback).where(
                HelpArticleFeedback.organization_id == organization_id,
                HelpArticleFeedback.person_id == person_id,
                HelpArticleFeedback.article_slug == slug,
            )
        )
        if existing:
            existing.rating = rating
            if comment is not None:
                existing.comment = comment
            self.db.flush()
            logger.info(
                "Help feedback updated: person=%s slug=%s rating=%s",
                person_id,
                slug,
                rating,
            )
        else:
            record = HelpArticleFeedback(
                organization_id=organization_id,
                person_id=person_id,
                article_slug=slug,
                rating=rating,
                comment=comment,
            )
            self.db.add(record)
            self.db.flush()
            logger.info(
                "Help feedback added: person=%s slug=%s rating=%s",
                person_id,
                slug,
                rating,
            )
        return rating

    def get_article_stats(
        self, organization_id: UUID, slug: str
    ) -> dict[str, int]:
        """Return feedback counts for an article."""
        stmt = (
            select(HelpArticleFeedback.rating, func.count())
            .where(
                HelpArticleFeedback.organization_id == organization_id,
                HelpArticleFeedback.article_slug == slug,
            )
            .group_by(HelpArticleFeedback.rating)
        )
        rows = self.db.execute(stmt).all()
        counts = {"helpful": 0, "not_helpful": 0}
        for rating, count in rows:
            counts[rating] = count
        return counts
