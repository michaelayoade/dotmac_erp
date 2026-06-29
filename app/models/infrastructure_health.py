import enum
import uuid
from datetime import datetime, timezone
from typing import Any

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class InfraHealthCategory(str, enum.Enum):
    APPLICATION = "application_services"
    SERVER = "server_health"
    WORKERS = "background_workers"
    SCHEDULED_JOBS = "scheduled_jobs"
    QUEUES = "queues"
    DATABASE = "database"
    REPLICATION = "standby_replication"
    CACHE = "redis_cache"
    EXTERNAL = "external_integrations"


class InfraHealthStatus(str, enum.Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class InfraAlertSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class InfraAlertStatus(str, enum.Enum):
    OPEN = "open"
    RESOLVED = "resolved"


class InfrastructureHealthStatus(Base):
    __tablename__ = "infrastructure_health_status"
    __table_args__ = (
        UniqueConstraint("category", "check_key", name="uq_infra_health_category_key"),
        Index("ix_infra_health_category_status", "category", "status"),
        Index("ix_infra_health_checked_at", "last_checked_at"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    category: Mapped[InfraHealthCategory] = mapped_column(
        Enum(InfraHealthCategory, name="infra_health_category"),
        nullable=False,
    )
    check_key: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[InfraHealthStatus] = mapped_column(
        Enum(InfraHealthStatus, name="infra_health_status"),
        nullable=False,
        default=InfraHealthStatus.UNKNOWN,
    )
    severity: Mapped[InfraAlertSeverity] = mapped_column(
        Enum(InfraAlertSeverity, name="infra_alert_severity"),
        nullable=False,
        default=InfraAlertSeverity.INFO,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    last_checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_healthy_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_unhealthy_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class InfrastructureAlert(Base):
    __tablename__ = "infrastructure_alert"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_infra_alert_fingerprint"),
        Index("ix_infra_alert_status_severity", "status", "severity"),
        Index("ix_infra_alert_category_status", "category", "status"),
        Index("ix_infra_alert_last_seen_at", "last_seen_at"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    fingerprint: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[InfraHealthCategory] = mapped_column(
        Enum(InfraHealthCategory, name="infra_health_category"),
        nullable=False,
    )
    check_key: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[InfraAlertSeverity] = mapped_column(
        Enum(InfraAlertSeverity, name="infra_alert_severity"),
        nullable=False,
    )
    status: Mapped[InfraAlertStatus] = mapped_column(
        Enum(InfraAlertStatus, name="infra_alert_status"),
        nullable=False,
        default=InfraAlertStatus.OPEN,
    )
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_notification_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
