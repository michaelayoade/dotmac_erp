"""
MetricStore — central read API for pre-computed analytics metrics.

All consumers (dashboards, AI coach, reports, exports) should read metrics
through this service rather than querying source tables directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.analytics.org_metric_snapshot import OrgMetricSnapshot

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MetricValue:
    """Immutable read-only metric snapshot value."""

    metric_type: str
    snapshot_date: date
    value_numeric: Decimal | None
    value_json: dict[str, Any] | None
    currency_code: str | None
    dimension_type: str
    dimension_id: str
    computed_at: Any  # datetime — Any to avoid SQLite compat issues


@dataclass(frozen=True)
class MetricComparison:
    """Period-over-period comparison result."""

    metric_type: str
    current_value: Decimal | None
    prior_value: Decimal | None
    delta: Decimal | None
    pct_change: float | None


class MetricStore:
    """Read API for pre-computed metric snapshots."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_latest(
        self,
        organization_id: UUID,
        metric_types: list[str],
        *,
        dimension_type: str = "ORG",
        dimension_id: str = "ALL",
    ) -> dict[str, MetricValue]:
        """Get the most recent snapshot for each requested metric type.

        Returns a dict keyed by metric_type. Missing metrics are omitted.
        """
        if not metric_types:
            return {}

        # Subquery: max snapshot_date per metric_type
        max_date_sq = (
            select(
                OrgMetricSnapshot.metric_type,
                func.max(OrgMetricSnapshot.snapshot_date).label("max_date"),
            )
            .where(
                OrgMetricSnapshot.organization_id == organization_id,
                OrgMetricSnapshot.metric_type.in_(metric_types),
                OrgMetricSnapshot.dimension_type == dimension_type,
                OrgMetricSnapshot.dimension_id == dimension_id,
            )
            .group_by(OrgMetricSnapshot.metric_type)
            .subquery()
        )

        stmt = (
            select(OrgMetricSnapshot)
            .join(
                max_date_sq,
                and_(
                    OrgMetricSnapshot.metric_type == max_date_sq.c.metric_type,
                    OrgMetricSnapshot.snapshot_date == max_date_sq.c.max_date,
                ),
            )
            .where(
                OrgMetricSnapshot.organization_id == organization_id,
                OrgMetricSnapshot.dimension_type == dimension_type,
                OrgMetricSnapshot.dimension_id == dimension_id,
            )
        )

        results: dict[str, MetricValue] = {}
        for row in self.db.scalars(stmt).all():
            results[row.metric_type] = _to_metric_value(row)

        return results

    def get_history(
        self,
        organization_id: UUID,
        metric_type: str,
        start_date: date,
        end_date: date,
        *,
        granularity: str = "DAILY",
        dimension_type: str = "ORG",
        dimension_id: str = "ALL",
    ) -> list[MetricValue]:
        """Get time-series data for a single metric type within a date range."""
        stmt = (
            select(OrgMetricSnapshot)
            .where(
                OrgMetricSnapshot.organization_id == organization_id,
                OrgMetricSnapshot.metric_type == metric_type,
                OrgMetricSnapshot.granularity == granularity,
                OrgMetricSnapshot.snapshot_date >= start_date,
                OrgMetricSnapshot.snapshot_date <= end_date,
                OrgMetricSnapshot.dimension_type == dimension_type,
                OrgMetricSnapshot.dimension_id == dimension_id,
            )
            .order_by(OrgMetricSnapshot.snapshot_date)
        )

        return [_to_metric_value(row) for row in self.db.scalars(stmt).all()]

    def get_prior_period(
        self,
        organization_id: UUID,
        metric_types: list[str],
        *,
        periods_back: int = 1,
        dimension_type: str = "ORG",
        dimension_id: str = "ALL",
    ) -> dict[str, MetricValue]:
        """Get metric values from N periods (days) ago.

        Finds the latest snapshot on or before (today - periods_back days).
        """
        if not metric_types:
            return {}

        cutoff = date.today() - timedelta(days=periods_back)

        # Subquery: max date on or before cutoff per metric_type
        max_date_sq = (
            select(
                OrgMetricSnapshot.metric_type,
                func.max(OrgMetricSnapshot.snapshot_date).label("max_date"),
            )
            .where(
                OrgMetricSnapshot.organization_id == organization_id,
                OrgMetricSnapshot.metric_type.in_(metric_types),
                OrgMetricSnapshot.snapshot_date <= cutoff,
                OrgMetricSnapshot.dimension_type == dimension_type,
                OrgMetricSnapshot.dimension_id == dimension_id,
            )
            .group_by(OrgMetricSnapshot.metric_type)
            .subquery()
        )

        stmt = (
            select(OrgMetricSnapshot)
            .join(
                max_date_sq,
                and_(
                    OrgMetricSnapshot.metric_type == max_date_sq.c.metric_type,
                    OrgMetricSnapshot.snapshot_date == max_date_sq.c.max_date,
                ),
            )
            .where(
                OrgMetricSnapshot.organization_id == organization_id,
                OrgMetricSnapshot.dimension_type == dimension_type,
                OrgMetricSnapshot.dimension_id == dimension_id,
            )
        )

        results: dict[str, MetricValue] = {}
        for row in self.db.scalars(stmt).all():
            results[row.metric_type] = _to_metric_value(row)

        return results

    def compare_periods(
        self,
        organization_id: UUID,
        metric_type: str,
        current_date: date,
        prior_date: date,
        *,
        dimension_type: str = "ORG",
        dimension_id: str = "ALL",
    ) -> MetricComparison:
        """Compare metric values between two specific dates.

        Returns delta (current - prior) and percentage change.
        """
        stmt = select(OrgMetricSnapshot).where(
            OrgMetricSnapshot.organization_id == organization_id,
            OrgMetricSnapshot.metric_type == metric_type,
            OrgMetricSnapshot.snapshot_date.in_([current_date, prior_date]),
            OrgMetricSnapshot.dimension_type == dimension_type,
            OrgMetricSnapshot.dimension_id == dimension_id,
        )

        by_date: dict[date, OrgMetricSnapshot] = {}
        for row in self.db.scalars(stmt).all():
            by_date[row.snapshot_date] = row

        current_row = by_date.get(current_date)
        prior_row = by_date.get(prior_date)

        current_val = (
            Decimal(str(current_row.value_numeric))
            if current_row and current_row.value_numeric is not None
            else None
        )
        prior_val = (
            Decimal(str(prior_row.value_numeric))
            if prior_row and prior_row.value_numeric is not None
            else None
        )

        delta: Decimal | None = None
        pct_change: float | None = None

        if current_val is not None and prior_val is not None:
            delta = current_val - prior_val
            if prior_val != 0:
                pct_change = float(delta / prior_val * 100)

        return MetricComparison(
            metric_type=metric_type,
            current_value=current_val,
            prior_value=prior_val,
            delta=delta,
            pct_change=pct_change,
        )


def _to_metric_value(row: OrgMetricSnapshot) -> MetricValue:
    """Convert an ORM row to a frozen MetricValue dataclass."""
    return MetricValue(
        metric_type=row.metric_type,
        snapshot_date=row.snapshot_date,
        value_numeric=(
            Decimal(str(row.value_numeric)) if row.value_numeric is not None else None
        ),
        value_json=row.value_json,
        currency_code=row.currency_code,
        dimension_type=row.dimension_type,
        dimension_id=row.dimension_id,
        computed_at=row.computed_at,
    )
