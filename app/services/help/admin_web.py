"""Help center admin web service.

Provides template context for admin article management UI.
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.help.models import (
    ArticleStatus,
    HelpArticleFeedback,
    HelpArticleOverride,
    HelpSearchEvent,
    HelpUserProgress,
)

logger = logging.getLogger(__name__)

MODULE_CHOICES = [
    ("finance", "Finance"),
    ("people", "People & HR"),
    ("inventory", "Inventory"),
    ("procurement", "Procurement"),
    ("support", "Support"),
    ("projects", "Projects"),
    ("expense", "Expenses"),
    ("fleet", "Fleet"),
    ("public_sector", "Public Sector"),
    ("coach", "Coach"),
    ("settings", "Settings"),
    ("self_service", "Self Service"),
]

CONTENT_TYPE_CHOICES = [
    ("workflow", "Workflow Guide"),
    ("quick_start", "Quick Start"),
    ("troubleshooting", "Troubleshooting"),
    ("cross_module_workflow", "Cross-Module Workflow"),
    ("admin_guide", "Admin Guide"),
    ("reference", "Reference"),
]


class HelpAdminWebService:
    """Web service for help center admin pages."""

    def __init__(self, db: Session):
        self.db = db

    def list_articles_context(
        self,
        organization_id: UUID,
        *,
        status: str | None = None,
        module_key: str | None = None,
        search: str | None = None,
        page: int = 1,
        per_page: int = 25,
    ) -> dict:
        """Build context for the admin article list page."""
        stmt = select(HelpArticleOverride).where(
            HelpArticleOverride.organization_id == organization_id
        )

        if status:
            stmt = stmt.where(HelpArticleOverride.status == ArticleStatus(status))
        if module_key:
            stmt = stmt.where(HelpArticleOverride.module_key == module_key)
        if search:
            stmt = stmt.where(
                HelpArticleOverride.title.ilike(f"%{search}%")
            )

        # Count
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = self.db.scalar(count_stmt) or 0

        # Paginate
        stmt = stmt.order_by(HelpArticleOverride.updated_at.desc())
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)
        articles = list(self.db.scalars(stmt).all())

        return {
            "articles": articles,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": max(1, (total + per_page - 1) // per_page),
            "status_filter": status or "",
            "module_filter": module_key or "",
            "search": search or "",
            "module_choices": MODULE_CHOICES,
            "status_choices": [
                ("", "All Status"),
                ("DRAFT", "Draft"),
                ("PUBLISHED", "Published"),
                ("ARCHIVED", "Archived"),
            ],
        }

    def article_form_context(
        self,
        organization_id: UUID,
        *,
        article_id: UUID | None = None,
    ) -> dict:
        """Build context for the article create/edit form."""
        article = None
        if article_id:
            article = self.db.scalar(
                select(HelpArticleOverride).where(
                    HelpArticleOverride.article_id == article_id,
                    HelpArticleOverride.organization_id == organization_id,
                )
            )

        return {
            "article": article,
            "module_choices": MODULE_CHOICES,
            "content_type_choices": CONTENT_TYPE_CHOICES,
            "is_edit": article is not None,
        }

    def create_article(
        self, organization_id: UUID, data: dict
    ) -> HelpArticleOverride:
        """Create a new admin-authored article."""
        article = HelpArticleOverride(
            organization_id=organization_id,
            slug=data["slug"],
            title=data["title"],
            summary=data.get("summary", ""),
            module_key=data["module_key"],
            content_type=data.get("content_type", "workflow"),
            status=ArticleStatus.DRAFT,
        )
        self.db.add(article)
        self.db.flush()
        logger.info("Help article created: %s", article.slug)
        return article

    def update_article(
        self, organization_id: UUID, article_id: UUID, data: dict
    ) -> HelpArticleOverride | None:
        """Update an existing article."""
        article = self.db.scalar(
            select(HelpArticleOverride).where(
                HelpArticleOverride.article_id == article_id,
                HelpArticleOverride.organization_id == organization_id,
            )
        )
        if not article:
            return None

        article.title = data["title"]
        article.slug = data["slug"]
        article.summary = data.get("summary", "")
        article.module_key = data["module_key"]
        article.content_type = data.get("content_type", "workflow")
        self.db.flush()
        logger.info("Help article updated: %s", article.slug)
        return article

    def publish_article(
        self, organization_id: UUID, article_id: UUID
    ) -> HelpArticleOverride | None:
        """Publish a draft/archived article."""
        article = self.db.scalar(
            select(HelpArticleOverride).where(
                HelpArticleOverride.article_id == article_id,
                HelpArticleOverride.organization_id == organization_id,
            )
        )
        if not article:
            return None
        article.status = ArticleStatus.PUBLISHED
        article.published_at = datetime.now(UTC)
        self.db.flush()
        logger.info("Help article published: %s", article.slug)
        return article

    def archive_article(
        self, organization_id: UUID, article_id: UUID
    ) -> HelpArticleOverride | None:
        """Archive a published article."""
        article = self.db.scalar(
            select(HelpArticleOverride).where(
                HelpArticleOverride.article_id == article_id,
                HelpArticleOverride.organization_id == organization_id,
            )
        )
        if not article:
            return None
        article.status = ArticleStatus.ARCHIVED
        self.db.flush()
        logger.info("Help article archived: %s", article.slug)
        return article

    def content_health_context(self, organization_id: UUID) -> dict:
        """Build context for the content health dashboard."""
        # Article counts by status
        status_counts_stmt = (
            select(
                HelpArticleOverride.status,
                func.count().label("count"),
            )
            .where(HelpArticleOverride.organization_id == organization_id)
            .group_by(HelpArticleOverride.status)
        )
        status_rows = self.db.execute(status_counts_stmt).all()
        status_counts = {str(row.status): row.count for row in status_rows}

        # Total feedback
        feedback_stats_stmt = (
            select(
                HelpArticleFeedback.rating,
                func.count().label("count"),
            )
            .where(HelpArticleFeedback.organization_id == organization_id)
            .group_by(HelpArticleFeedback.rating)
        )
        feedback_rows = self.db.execute(feedback_stats_stmt).all()
        feedback_counts = {"helpful": 0, "not_helpful": 0}
        for row in feedback_rows:
            feedback_counts[row.rating] = int(row._mapping["count"])

        # Total completions
        completion_count = self.db.scalar(
            select(func.count()).where(
                HelpUserProgress.organization_id == organization_id
            )
        ) or 0

        # Total searches
        search_count = self.db.scalar(
            select(func.count()).where(
                HelpSearchEvent.organization_id == organization_id
            )
        ) or 0

        # Popular searches
        popular_stmt = (
            select(
                HelpSearchEvent.query,
                func.count().label("search_count"),
            )
            .where(HelpSearchEvent.organization_id == organization_id)
            .group_by(HelpSearchEvent.query)
            .order_by(func.count().desc())
            .limit(10)
        )
        popular_searches = [
            {"query": row.query, "count": row.search_count}
            for row in self.db.execute(popular_stmt).all()
        ]

        # Zero-result searches
        zero_result_stmt = (
            select(
                HelpSearchEvent.query,
                func.count().label("search_count"),
            )
            .where(
                HelpSearchEvent.organization_id == organization_id,
                HelpSearchEvent.result_count == 0,
            )
            .group_by(HelpSearchEvent.query)
            .order_by(func.count().desc())
            .limit(10)
        )
        zero_result_searches = [
            {"query": row.query, "count": row.search_count}
            for row in self.db.execute(zero_result_stmt).all()
        ]

        return {
            "status_counts": status_counts,
            "feedback_counts": feedback_counts,
            "completion_count": completion_count,
            "search_count": search_count,
            "popular_searches": popular_searches,
            "zero_result_searches": zero_result_searches,
        }
