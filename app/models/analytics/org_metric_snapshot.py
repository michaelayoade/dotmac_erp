"""
OrgMetricSnapshot model.

Generic time-series table for pre-computed scalar/aggregate KPIs.
Complements domain-specific snapshots (ar_aging_snapshot, account_balance, etc.)
by storing cross-module, roll-up metrics in a single queryable table.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

_JSON = JSON().with_variant(JSONB, "postgresql")


class MetricGranularity(str, enum.Enum):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


class OrgMetricSnapshot(Base):
    """Pre-computed metric snapshot row.

    One row per (org, metric_type, date, granularity, dimension) combination.
    """

    __tablename__ = "org_metric_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "metric_type",
            "snapshot_date",
            "granularity",
            "dimension_type",
            "dimension_id",
            name="uq_org_metric_snapshot_key",
        ),
        Index(
            "idx_oms_org_type_date",
            "organization_id",
            "metric_type",
            "snapshot_date",
        ),
        Index(
            "idx_oms_org_type_gran_date",
            "organization_id",
            "metric_type",
            "granularity",
            "snapshot_date",
        ),
        Index(
            "idx_oms_org_dim_type",
            "organization_id",
            "dimension_type",
            "dimension_id",
            "metric_type",
        ),
    )

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    metric_type: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
    )
    snapshot_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    granularity: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="DAILY",
    )
    dimension_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="ORG",
    )
    dimension_id: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="ALL",
    )
    value_numeric: Mapped[float | None] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )
    value_json: Mapped[dict | None] = mapped_column(
        _JSON,
        nullable=True,
    )
    currency_code: Mapped[str | None] = mapped_column(
        String(3),
        nullable=True,
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    source_label: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<OrgMetricSnapshot({self.metric_type}, "
            f"date={self.snapshot_date}, "
            f"value={self.value_numeric})>"
        )
