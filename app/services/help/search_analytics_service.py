"""Help search analytics service.

Records search queries and provides analytics for admin insights.
"""

import logging
from uuid import UUID

from sqlalchemy import func, select

from app.models.help.models import HelpSearchEvent

logger = logging.getLogger(__name__)


class HelpSearchAnalyticsService:
    """Service for recording and querying help search analytics."""

    def __init__(self, db):
        self.db = db

    def record_search(
        self,
        organization_id: UUID,
        query: str,
        result_count: int,
        *,
        person_id: UUID | None = None,
        filters: dict | None = None,
        clicked_slug: str | None = None,
    ) -> None:
        """Record a search event."""
        event = HelpSearchEvent(
            organization_id=organization_id,
            person_id=person_id,
            query=query.strip()[:500],
            filters=filters,
            result_count=result_count,
            clicked_slug=clicked_slug,
        )
        self.db.add(event)
        self.db.flush()

    def get_popular_searches(
        self, organization_id: UUID, limit: int = 20
    ) -> list[dict]:
        """Return popular searches ordered by frequency."""
        stmt = (
            select(
                HelpSearchEvent.query,
                func.count().label("search_count"),
                func.avg(HelpSearchEvent.result_count).label("avg_results"),
            )
            .where(HelpSearchEvent.organization_id == organization_id)
            .group_by(HelpSearchEvent.query)
            .order_by(func.count().desc())
            .limit(limit)
        )
        rows = self.db.execute(stmt).all()
        return [
            {
                "query": row.query,
                "search_count": row.search_count,
                "avg_results": round(float(row.avg_results or 0), 1),
            }
            for row in rows
        ]

    def get_zero_result_queries(
        self, organization_id: UUID, limit: int = 20
    ) -> list[dict]:
        """Return searches that returned zero results."""
        stmt = (
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
            .limit(limit)
        )
        rows = self.db.execute(stmt).all()
        return [{"query": row.query, "search_count": row.search_count} for row in rows]
