"""Coach analyzers (deterministic analysis + optional LLM narration).

Provides ``metric_is_fresh()`` helper for MetricStore-based fast paths.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

_MAX_STALE_HOURS = 24


def metric_is_fresh(
    db: Session,
    organization_id: UUID,
    metric_type: str,
    *,
    max_age_hours: int = _MAX_STALE_HOURS,
) -> tuple[bool, Decimal | None]:
    """Check if a pre-computed metric is fresh and return its value.

    Returns (is_fresh, value_numeric).  If no snapshot exists or it's older
    than *max_age_hours*, returns ``(False, None)`` so the caller should
    fall back to a live query.
    """
    from app.services.analytics.metric_store import MetricStore

    store = MetricStore(db)
    latest = store.get_latest(organization_id, [metric_type])

    mv = latest.get(metric_type)
    if mv is None:
        return False, None

    cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
    computed = mv.computed_at
    if computed.tzinfo is None:
        computed = computed.replace(tzinfo=UTC)

    if computed < cutoff:
        return False, None

    return True, mv.value_numeric
