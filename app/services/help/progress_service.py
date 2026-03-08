"""Help article progress tracking service.

Manages article completion state per user, persisted in the database.
"""

import logging
from uuid import UUID

from sqlalchemy import delete, select

from app.models.help.models import HelpUserProgress

logger = logging.getLogger(__name__)


class HelpProgressService:
    """Service for tracking help article completion per user."""

    def __init__(self, db):
        self.db = db

    def get_completed_slugs(self, organization_id: UUID, person_id: UUID) -> list[str]:
        """Return all article slugs the user has completed."""
        stmt = select(HelpUserProgress.article_slug).where(
            HelpUserProgress.organization_id == organization_id,
            HelpUserProgress.person_id == person_id,
        )
        return list(self.db.scalars(stmt).all())

    def is_completed(self, organization_id: UUID, person_id: UUID, slug: str) -> bool:
        """Check if a specific article is completed."""
        stmt = select(HelpUserProgress.progress_id).where(
            HelpUserProgress.organization_id == organization_id,
            HelpUserProgress.person_id == person_id,
            HelpUserProgress.article_slug == slug,
        )
        return self.db.scalar(stmt) is not None

    def toggle_completion(
        self, organization_id: UUID, person_id: UUID, slug: str
    ) -> bool:
        """Toggle article completion. Returns True if now completed, False if uncompleted."""
        existing = self.db.scalar(
            select(HelpUserProgress.progress_id).where(
                HelpUserProgress.organization_id == organization_id,
                HelpUserProgress.person_id == person_id,
                HelpUserProgress.article_slug == slug,
            )
        )
        if existing:
            self.db.execute(
                delete(HelpUserProgress).where(HelpUserProgress.progress_id == existing)
            )
            self.db.flush()
            logger.info("Help progress removed: person=%s slug=%s", person_id, slug)
            return False

        record = HelpUserProgress(
            organization_id=organization_id,
            person_id=person_id,
            article_slug=slug,
        )
        self.db.add(record)
        self.db.flush()
        logger.info("Help progress added: person=%s slug=%s", person_id, slug)
        return True
