"""Read-only service for published SLA policy content."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.help.models import ArticleStatus, HelpArticleOverride


class SLAPolicyReadService:
    """Load the published SLA policies visible to an organization."""

    MODULE_KEY = "sla_policies"
    CONTENT_TYPE = "sla_policy"

    def __init__(self, db: Session):
        self.db = db

    def list_published_for_org(
        self, organization_id: UUID
    ) -> list[HelpArticleOverride]:
        """Return only published SLA policy rows for the organization."""
        stmt = (
            select(HelpArticleOverride)
            .where(
                HelpArticleOverride.organization_id == organization_id,
                HelpArticleOverride.module_key == self.MODULE_KEY,
                HelpArticleOverride.content_type == self.CONTENT_TYPE,
                HelpArticleOverride.status == ArticleStatus.PUBLISHED,
            )
            .order_by(
                HelpArticleOverride.published_at.desc().nullslast(),
                HelpArticleOverride.title.asc(),
            )
        )
        return list(self.db.scalars(stmt).all())
