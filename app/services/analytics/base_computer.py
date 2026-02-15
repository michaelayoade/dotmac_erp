"""
BaseComputer — abstract base for metric computation classes.

Each concrete computer (CashFlowComputer, EfficiencyComputer, etc.)
implements ``compute_for_org()`` and uses ``upsert_metric()`` to
write results into ``org_metric_snapshot``.
"""

from __future__ import annotations

import abc
import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete
from sqlalchemy.orm import Session

from app.models.analytics.org_metric_snapshot import OrgMetricSnapshot

logger = logging.getLogger(__name__)


class BaseComputer(abc.ABC):
    """Abstract base class for metric computers."""

    METRIC_TYPES: list[str] = []
    SOURCE_LABEL: str = ""

    def __init__(self, db: Session) -> None:
        self.db = db

    @abc.abstractmethod
    def compute_for_org(
        self,
        organization_id: UUID,
        snapshot_date: date,
    ) -> int:
        """Compute and upsert all metrics for a single org.

        Returns the number of metrics written.
        """
        ...

    def upsert_metric(
        self,
        *,
        organization_id: UUID,
        metric_type: str,
        snapshot_date: date,
        value_numeric: Decimal | float | None = None,
        value_json: dict[str, Any] | None = None,
        currency_code: str | None = None,
        granularity: str = "DAILY",
        dimension_type: str = "ORG",
        dimension_id: str = "ALL",
    ) -> None:
        """Upsert a single metric row.

        Uses PostgreSQL ``INSERT ... ON CONFLICT DO UPDATE`` for production,
        and DELETE + INSERT for SQLite tests.
        """
        bind = self.db.bind
        dialect_name = bind.dialect.name if bind is not None else "unknown"

        now = datetime.now(UTC)
        numeric_val = Decimal(str(value_numeric)) if value_numeric is not None else None

        if dialect_name == "postgresql":
            self._upsert_pg(
                organization_id=organization_id,
                metric_type=metric_type,
                snapshot_date=snapshot_date,
                granularity=granularity,
                dimension_type=dimension_type,
                dimension_id=dimension_id,
                value_numeric=numeric_val,
                value_json=value_json,
                currency_code=currency_code,
                computed_at=now,
            )
        else:
            # SQLite fallback: DELETE + INSERT
            self._upsert_sqlite(
                organization_id=organization_id,
                metric_type=metric_type,
                snapshot_date=snapshot_date,
                granularity=granularity,
                dimension_type=dimension_type,
                dimension_id=dimension_id,
                value_numeric=numeric_val,
                value_json=value_json,
                currency_code=currency_code,
                computed_at=now,
            )

    def _upsert_pg(
        self,
        *,
        organization_id: UUID,
        metric_type: str,
        snapshot_date: date,
        granularity: str,
        dimension_type: str,
        dimension_id: str,
        value_numeric: Decimal | None,
        value_json: dict[str, Any] | None,
        currency_code: str | None,
        computed_at: datetime,
    ) -> None:
        """PostgreSQL upsert via INSERT ... ON CONFLICT DO UPDATE."""
        from sqlalchemy.dialects.postgresql import insert

        values = {
            "organization_id": organization_id,
            "metric_type": metric_type,
            "snapshot_date": snapshot_date,
            "granularity": granularity,
            "dimension_type": dimension_type,
            "dimension_id": dimension_id,
            "value_numeric": value_numeric,
            "value_json": value_json,
            "currency_code": currency_code,
            "computed_at": computed_at,
            "source_label": self.SOURCE_LABEL,
        }

        stmt = insert(OrgMetricSnapshot).values(**values)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_org_metric_snapshot_key",
            set_={
                "value_numeric": stmt.excluded.value_numeric,
                "value_json": stmt.excluded.value_json,
                "currency_code": stmt.excluded.currency_code,
                "computed_at": stmt.excluded.computed_at,
                "source_label": stmt.excluded.source_label,
            },
        )
        self.db.execute(stmt)

    def _upsert_sqlite(
        self,
        *,
        organization_id: UUID,
        metric_type: str,
        snapshot_date: date,
        granularity: str,
        dimension_type: str,
        dimension_id: str,
        value_numeric: Decimal | None,
        value_json: dict[str, Any] | None,
        currency_code: str | None,
        computed_at: datetime,
    ) -> None:
        """SQLite-compatible upsert: DELETE existing + INSERT new."""
        self.db.execute(
            delete(OrgMetricSnapshot).where(
                and_(
                    OrgMetricSnapshot.organization_id == organization_id,
                    OrgMetricSnapshot.metric_type == metric_type,
                    OrgMetricSnapshot.snapshot_date == snapshot_date,
                    OrgMetricSnapshot.granularity == granularity,
                    OrgMetricSnapshot.dimension_type == dimension_type,
                    OrgMetricSnapshot.dimension_id == dimension_id,
                )
            )
        )

        row = OrgMetricSnapshot(
            organization_id=organization_id,
            metric_type=metric_type,
            snapshot_date=snapshot_date,
            granularity=granularity,
            dimension_type=dimension_type,
            dimension_id=dimension_id,
            value_numeric=value_numeric,
            value_json=value_json,
            currency_code=currency_code,
            computed_at=computed_at,
            source_label=self.SOURCE_LABEL,
        )
        self.db.add(row)
        self.db.flush()
