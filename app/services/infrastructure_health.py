from __future__ import annotations

import logging
import os
import platform
import shutil
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from collections.abc import Callable
from typing import Any, cast

import redis
from celery.exceptions import CeleryError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.db.session_context import for_each_organization
from app.dependency_health import collect_dependency_health
from app.models.infrastructure_health import (
    InfraAlertSeverity,
    InfraAlertStatus,
    InfraHealthCategory,
    InfraHealthStatus,
    InfrastructureAlert,
    InfrastructureHealthStatus,
)
from app.models.notification import (
    EntityType,
    NotificationChannel,
    NotificationType,
)
from app.models.person import Person
from app.models.rbac import Permission, PersonRole, Role, RolePermission
from app.models.scheduler import ScheduledTask
from app.monitoring import get_monitoring_status
from app.services.notification import notification_service
from app.telemetry import get_otel_status

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

logger = logging.getLogger(__name__)

MONITORING_READ_PERMISSIONS = (
    "system:health:read",
    "system:alerts:read",
    "system:alerts:manage",
)


@dataclass(frozen=True)
class AlertNotificationEvent:
    alert_id: uuid.UUID
    title: str
    summary: str
    prefix: str


@dataclass(frozen=True)
class HealthCheckResult:
    category: InfraHealthCategory
    check_key: str
    display_name: str
    status: InfraHealthStatus
    severity: InfraAlertSeverity
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    last_activity_at: datetime | None = None

    @property
    def fingerprint(self) -> str:
        return f"{self.category.value}:{self.check_key}"

    @property
    def is_problem(self) -> bool:
        return self.status in {
            InfraHealthStatus.DEGRADED,
            InfraHealthStatus.UNHEALTHY,
            InfraHealthStatus.UNKNOWN,
        }


class InfrastructureHealthService:
    def collect_checks(self, db: Session) -> list[HealthCheckResult]:
        checks: list[HealthCheckResult] = []
        checks.extend(self._application_checks())
        checks.extend(self._server_checks())
        checks.append(self._database_check(db))
        checks.extend(self._replication_checks(db))
        checks.append(self._redis_check())
        checks.extend(self._celery_worker_checks())
        checks.extend(self._queue_checks())
        checks.extend(self._scheduled_job_checks(db))
        checks.extend(self._external_dependency_checks())
        return checks

    def run_checks(self, db: Session) -> dict[str, Any]:
        now = datetime.now(UTC)
        checks = self.collect_checks(db)
        seen_fingerprints = set()
        notification_events: list[AlertNotificationEvent] = []
        created = reopened = escalated = resolved = 0

        for result in checks:
            seen_fingerprints.add(result.fingerprint)
            self._upsert_status(db, result, now)

            if result.is_problem:
                event = self._upsert_alert(db, result, now)
                event_name = str(event["event"]) if event else ""
                if event_name == "created":
                    created += 1
                elif event_name == "reopened":
                    reopened += 1
                elif event_name == "escalated":
                    escalated += 1
                if event:
                    notification_events.append(
                        AlertNotificationEvent(
                            alert_id=cast(uuid.UUID, event["alert_id"]),
                            title=str(event["title"]),
                            summary=str(event["summary"]),
                            prefix=str(event["prefix"]),
                        )
                    )
            else:
                if self._resolve_alert(db, result.fingerprint, now):
                    resolved += 1

        self._resolve_missing_check_alerts(db, seen_fingerprints, now)
        db.flush()

        return {
            "checks": len(checks),
            "created": created,
            "reopened": reopened,
            "escalated": escalated,
            "resolved": resolved,
            "notification_events": notification_events,
        }

    def dashboard_summary(self, db: Session) -> dict[str, Any]:
        alerts = list(
            db.scalars(
                select(InfrastructureAlert)
                .where(InfrastructureAlert.status == InfraAlertStatus.OPEN)
                .order_by(
                    InfrastructureAlert.severity.desc(),
                    InfrastructureAlert.last_seen_at.desc(),
                )
                .limit(5)
            )
        )
        counts = Counter(alert.severity for alert in alerts)
        open_total = (
            db.scalar(
                select(func.count(InfrastructureAlert.id)).where(
                    InfrastructureAlert.status == InfraAlertStatus.OPEN
                )
            )
            or 0
        )
        worst = self._worst_status(db)
        return {
            "open_alerts": alerts,
            "open_count": int(open_total),
            "critical_count": int(counts.get(InfraAlertSeverity.CRITICAL, 0)),
            "warning_count": int(counts.get(InfraAlertSeverity.WARNING, 0)),
            "status": worst.value.title(),
            "status_raw": worst.value,
        }

    def health_page_context(self, db: Session) -> dict[str, Any]:
        statuses = list(
            db.scalars(
                select(InfrastructureHealthStatus).order_by(
                    InfrastructureHealthStatus.category,
                    InfrastructureHealthStatus.display_name,
                )
            )
        )
        grouped: dict[InfraHealthCategory, list[InfrastructureHealthStatus]] = (
            defaultdict(list)
        )
        for status in statuses:
            grouped[status.category].append(status)

        return {
            "categories": [
                {
                    "key": category.value,
                    "label": self.category_label(category),
                    "checks": grouped.get(category, []),
                }
                for category in InfraHealthCategory
            ],
            "summary": self.dashboard_summary(db),
        }

    def alerts_page_context(
        self,
        db: Session,
        *,
        category: str = "",
        severity: str = "",
        status: str = "",
        period: str = "7d",
    ) -> dict[str, Any]:
        stmt = select(InfrastructureAlert)
        category_enum = self._parse_category(category)
        severity_enum = self._parse_severity(severity)
        status_enum = self._parse_alert_status(status)
        since = self._period_start(period)

        if category_enum:
            stmt = stmt.where(InfrastructureAlert.category == category_enum)
        if severity_enum:
            stmt = stmt.where(InfrastructureAlert.severity == severity_enum)
        if status_enum:
            stmt = stmt.where(InfrastructureAlert.status == status_enum)
        if since:
            stmt = stmt.where(InfrastructureAlert.last_seen_at >= since)

        alerts = list(
            db.scalars(
                stmt.order_by(InfrastructureAlert.last_seen_at.desc()).limit(200)
            )
        )
        return {
            "alerts": alerts,
            "filters": {
                "category": category,
                "severity": severity,
                "status": status,
                "period": period,
            },
            "categories": [
                {"value": item.value, "label": self.category_label(item)}
                for item in InfraHealthCategory
            ],
            "severities": [item.value for item in InfraAlertSeverity],
            "statuses": [item.value for item in InfraAlertStatus],
            "periods": [
                {"value": "24h", "label": "24 hours"},
                {"value": "7d", "label": "7 days"},
                {"value": "30d", "label": "30 days"},
                {"value": "all", "label": "All time"},
            ],
        }

    def get_alert(self, db: Session, alert_id: uuid.UUID) -> InfrastructureAlert | None:
        return db.get(InfrastructureAlert, alert_id)

    def category_label(self, category: InfraHealthCategory) -> str:
        labels = {
            InfraHealthCategory.APPLICATION: "Application Services",
            InfraHealthCategory.SERVER: "Server Health",
            InfraHealthCategory.WORKERS: "Background Workers",
            InfraHealthCategory.SCHEDULED_JOBS: "Scheduled Jobs",
            InfraHealthCategory.QUEUES: "Queues and Backlogs",
            InfraHealthCategory.DATABASE: "Database Connectivity",
            InfraHealthCategory.REPLICATION: "Standby Replication",
            InfraHealthCategory.CACHE: "Redis and Cache",
            InfraHealthCategory.EXTERNAL: "External Integrations",
        }
        return labels[category]

    def _application_checks(self) -> list[HealthCheckResult]:
        monitoring = get_monitoring_status()
        otel = get_otel_status()
        loki = monitoring.get("loki", {})
        sentry = monitoring.get("sentry", {})
        loki_enabled = bool(loki.get("enabled"))
        loki_consecutive_failures = int(loki.get("consecutive_failures") or 0)
        loki_healthy = not loki_enabled or loki_consecutive_failures < 5
        if not loki_enabled:
            loki_summary = "Loki logging disabled"
        elif loki_healthy:
            loki_summary = (
                "Loki logging healthy; "
                f"{loki_consecutive_failures} consecutive failure(s)"
            )
        else:
            loki_summary = (
                "Loki logging degraded; "
                f"{loki_consecutive_failures} consecutive failure(s)"
            )

        checks = [
            HealthCheckResult(
                category=InfraHealthCategory.APPLICATION,
                check_key="web_app",
                display_name="ERP Web Application",
                status=InfraHealthStatus.HEALTHY,
                severity=InfraAlertSeverity.INFO,
                summary="Application process is responding",
                details={"environment": os.getenv("APP_ENV", "production")},
                last_activity_at=datetime.now(UTC),
            ),
            HealthCheckResult(
                category=InfraHealthCategory.APPLICATION,
                check_key="loki_logging",
                display_name="Loki Logging",
                status=InfraHealthStatus.HEALTHY
                if loki_healthy
                else InfraHealthStatus.DEGRADED,
                severity=InfraAlertSeverity.WARNING,
                summary=loki_summary,
                details=dict(loki),
            ),
            HealthCheckResult(
                category=InfraHealthCategory.APPLICATION,
                check_key="sentry_glitchtip",
                display_name="Sentry/GlitchTip",
                status=InfraHealthStatus.HEALTHY
                if not sentry.get("enabled") or bool(sentry.get("transport_healthy"))
                else InfraHealthStatus.DEGRADED,
                severity=InfraAlertSeverity.WARNING,
                summary="Error monitoring is healthy"
                if not sentry.get("enabled") or bool(sentry.get("transport_healthy"))
                else "Error monitoring transport is unhealthy",
                details=dict(sentry),
            ),
            HealthCheckResult(
                category=InfraHealthCategory.APPLICATION,
                check_key="opentelemetry",
                display_name="OpenTelemetry",
                status=InfraHealthStatus.HEALTHY
                if not otel.get("enabled") or bool(otel.get("configured"))
                else InfraHealthStatus.DEGRADED,
                severity=InfraAlertSeverity.INFO,
                summary="OpenTelemetry configured"
                if otel.get("enabled")
                else "OpenTelemetry disabled",
                details=dict(otel),
            ),
        ]
        return checks

    def _server_checks(self) -> list[HealthCheckResult]:
        details: dict[str, Any] = {"hostname": platform.node() or None}
        status = InfraHealthStatus.HEALTHY
        severity = InfraAlertSeverity.INFO
        summary_parts: list[str] = []

        try:
            usage = shutil.disk_usage("/")
            disk_used_percent = round((usage.used / usage.total) * 100, 1)
            details["disk"] = {
                "path": "/",
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
                "used_percent": disk_used_percent,
            }
            summary_parts.append(f"disk {disk_used_percent}% used")
            if disk_used_percent >= 95:
                status = InfraHealthStatus.UNHEALTHY
                severity = InfraAlertSeverity.CRITICAL
            elif disk_used_percent >= 85 and status != InfraHealthStatus.UNHEALTHY:
                status = InfraHealthStatus.DEGRADED
                severity = InfraAlertSeverity.WARNING
        except Exception as exc:
            details["disk_error"] = str(exc)[:240]
            status = InfraHealthStatus.UNKNOWN
            severity = InfraAlertSeverity.WARNING

        memory = self._linux_memory_usage()
        if memory:
            details["memory"] = memory
            used_percent = memory["used_percent"]
            summary_parts.append(f"memory {used_percent}% used")
            if used_percent >= 95:
                status = InfraHealthStatus.UNHEALTHY
                severity = InfraAlertSeverity.CRITICAL
            elif used_percent >= 90 and status != InfraHealthStatus.UNHEALTHY:
                status = InfraHealthStatus.DEGRADED
                severity = InfraAlertSeverity.WARNING

        load = self._load_average()
        if load:
            details["load"] = load
            summary_parts.append(f"load {load['one_minute_per_cpu']} per CPU")
            if load["one_minute_per_cpu"] >= 4:
                status = InfraHealthStatus.UNHEALTHY
                severity = InfraAlertSeverity.CRITICAL
            elif (
                load["one_minute_per_cpu"] >= 2
                and status != InfraHealthStatus.UNHEALTHY
            ):
                status = InfraHealthStatus.DEGRADED
                severity = InfraAlertSeverity.WARNING

        uptime_seconds = self._linux_uptime_seconds()
        if uptime_seconds is not None:
            details["uptime_seconds"] = uptime_seconds

        return [
            HealthCheckResult(
                category=InfraHealthCategory.SERVER,
                check_key="server_resources",
                display_name="Server Resources",
                status=status,
                severity=severity,
                summary=", ".join(summary_parts)
                if summary_parts
                else "Server metrics unavailable",
                details=details,
                last_activity_at=datetime.now(UTC),
            )
        ]

    def _database_check(self, db: Session) -> HealthCheckResult:
        try:
            version = db.scalar(text("select version()"))
            return HealthCheckResult(
                category=InfraHealthCategory.DATABASE,
                check_key="primary_database",
                display_name="Primary PostgreSQL",
                status=InfraHealthStatus.HEALTHY,
                severity=InfraAlertSeverity.CRITICAL,
                summary="Primary database connection succeeded",
                details={"version": str(version).split(",", 1)[0] if version else None},
                last_activity_at=datetime.now(UTC),
            )
        except Exception as exc:
            return HealthCheckResult(
                category=InfraHealthCategory.DATABASE,
                check_key="primary_database",
                display_name="Primary PostgreSQL",
                status=InfraHealthStatus.UNHEALTHY,
                severity=InfraAlertSeverity.CRITICAL,
                summary="Primary database connection failed",
                details={"error": str(exc)[:240]},
            )

    def _replication_checks(self, db: Session) -> list[HealthCheckResult]:
        try:
            rows = list(
                db.execute(
                    text(
                        """
                        select
                            application_name,
                            client_addr::text as client_addr,
                            state,
                            sync_state,
                            replay_lag,
                            pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) as bytes_behind
                        from pg_stat_replication
                        order by client_addr nulls last, application_name
                        """
                    )
                ).mappings()
            )
            slots = list(
                db.execute(
                    text(
                        """
                        select slot_name, active, restart_lsn,
                               pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) as retained_bytes
                        from pg_replication_slots
                        where slot_type = 'physical'
                        order by slot_name
                        """
                    )
                ).mappings()
            )
        except Exception as exc:
            return [
                HealthCheckResult(
                    category=InfraHealthCategory.REPLICATION,
                    check_key="primary_replication_view",
                    display_name="Primary Replication View",
                    status=InfraHealthStatus.UNHEALTHY,
                    severity=InfraAlertSeverity.CRITICAL,
                    summary="Could not read PostgreSQL replication status",
                    details={"error": str(exc)[:240]},
                )
            ]

        active_rows = [
            row for row in rows if str(row.get("state") or "") == "streaming"
        ]
        max_bytes = max((int(row.get("bytes_behind") or 0) for row in rows), default=0)
        active_slots = [slot for slot in slots if bool(slot.get("active"))]
        status = (
            InfraHealthStatus.HEALTHY
            if active_rows and active_slots
            else InfraHealthStatus.DEGRADED
        )
        severity = (
            InfraAlertSeverity.CRITICAL
            if not active_rows
            else InfraAlertSeverity.WARNING
        )
        summary = (
            f"{len(active_rows)} streaming standby connection(s), {max_bytes} bytes behind"
            if active_rows
            else "No active streaming standby connection found"
        )
        return [
            HealthCheckResult(
                category=InfraHealthCategory.REPLICATION,
                check_key="physical_streaming_replication",
                display_name="Physical Streaming Replication",
                status=status,
                severity=severity,
                summary=summary,
                details={
                    "standbys": [dict(row) for row in rows],
                    "slots": [dict(slot) for slot in slots],
                    "bytes_behind": max_bytes,
                },
                last_activity_at=datetime.now(UTC) if active_rows else None,
            )
        ]

    def _redis_check(self) -> HealthCheckResult:
        url = os.getenv("REDIS_URL")
        if not url:
            return HealthCheckResult(
                category=InfraHealthCategory.CACHE,
                check_key="redis",
                display_name="Redis Cache",
                status=InfraHealthStatus.DEGRADED,
                severity=InfraAlertSeverity.WARNING,
                summary="REDIS_URL is not configured",
                details={},
            )
        try:
            client = redis.Redis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
            client.ping()
            info = cast(dict[str, Any], client.info(section="server"))
            return HealthCheckResult(
                category=InfraHealthCategory.CACHE,
                check_key="redis",
                display_name="Redis Cache",
                status=InfraHealthStatus.HEALTHY,
                severity=InfraAlertSeverity.CRITICAL,
                summary="Redis ping succeeded",
                details={"redis_version": info.get("redis_version")},
                last_activity_at=datetime.now(UTC),
            )
        except Exception as exc:
            return HealthCheckResult(
                category=InfraHealthCategory.CACHE,
                check_key="redis",
                display_name="Redis Cache",
                status=InfraHealthStatus.UNHEALTHY,
                severity=InfraAlertSeverity.CRITICAL,
                summary="Redis ping failed",
                details={"error": str(exc)[:240]},
            )

    def _celery_worker_checks(self) -> list[HealthCheckResult]:
        try:
            from app.celery_app import celery_app

            inspector = celery_app.control.inspect(timeout=1.5)
            pings = inspector.ping() or {}
            stats = inspector.stats() or {}
        except (CeleryError, Exception) as exc:
            return [
                HealthCheckResult(
                    category=InfraHealthCategory.WORKERS,
                    check_key="celery_workers",
                    display_name="Celery Workers",
                    status=InfraHealthStatus.UNHEALTHY,
                    severity=InfraAlertSeverity.CRITICAL,
                    summary="Could not inspect Celery workers",
                    details={"error": str(exc)[:240]},
                )
            ]

        worker_count = len(pings)
        status = (
            InfraHealthStatus.HEALTHY if worker_count else InfraHealthStatus.UNHEALTHY
        )
        return [
            HealthCheckResult(
                category=InfraHealthCategory.WORKERS,
                check_key="celery_workers",
                display_name="Celery Workers",
                status=status,
                severity=InfraAlertSeverity.CRITICAL,
                summary=f"{worker_count} worker(s) responding"
                if worker_count
                else "No Celery workers responded",
                details={"workers": sorted(pings.keys()), "stats": stats},
                last_activity_at=datetime.now(UTC) if worker_count else None,
            )
        ]

    def _queue_checks(self) -> list[HealthCheckResult]:
        url = os.getenv("REDIS_URL")
        if not url:
            return [
                HealthCheckResult(
                    category=InfraHealthCategory.QUEUES,
                    check_key="celery_backlog",
                    display_name="Celery Queue Backlog",
                    status=InfraHealthStatus.UNKNOWN,
                    severity=InfraAlertSeverity.WARNING,
                    summary="Queue backlog unavailable because Redis is not configured",
                )
            ]
        try:
            client = redis.Redis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
            queue_names = [
                item.strip()
                for item in os.getenv(
                    "INFRA_HEALTH_QUEUE_NAMES", "celery,default"
                ).split(",")
                if item.strip()
            ]
            lengths = {
                queue: int(cast(int, client.llen(queue))) for queue in queue_names
            }
            total = sum(lengths.values())
            if total >= int(os.getenv("INFRA_HEALTH_QUEUE_CRITICAL_THRESHOLD", "1000")):
                status = InfraHealthStatus.UNHEALTHY
                severity = InfraAlertSeverity.CRITICAL
            elif total >= int(os.getenv("INFRA_HEALTH_QUEUE_WARNING_THRESHOLD", "100")):
                status = InfraHealthStatus.DEGRADED
                severity = InfraAlertSeverity.WARNING
            else:
                status = InfraHealthStatus.HEALTHY
                severity = InfraAlertSeverity.INFO
            return [
                HealthCheckResult(
                    category=InfraHealthCategory.QUEUES,
                    check_key="celery_backlog",
                    display_name="Celery Queue Backlog",
                    status=status,
                    severity=severity,
                    summary=f"{total} queued task(s)",
                    details={"queues": lengths},
                    last_activity_at=datetime.now(UTC),
                )
            ]
        except Exception as exc:
            return [
                HealthCheckResult(
                    category=InfraHealthCategory.QUEUES,
                    check_key="celery_backlog",
                    display_name="Celery Queue Backlog",
                    status=InfraHealthStatus.UNKNOWN,
                    severity=InfraAlertSeverity.WARNING,
                    summary="Could not read queue backlog",
                    details={"error": str(exc)[:240]},
                )
            ]

    def _scheduled_job_checks(self, db: Session) -> list[HealthCheckResult]:
        enabled_count = (
            db.scalar(
                select(func.count(ScheduledTask.id)).where(
                    ScheduledTask.enabled.is_(True)
                )
            )
            or 0
        )
        stale_cutoff = datetime.now(UTC) - timedelta(hours=24)
        stale_count = (
            db.scalar(
                select(func.count(ScheduledTask.id))
                .where(ScheduledTask.enabled.is_(True))
                .where(ScheduledTask.last_run_at.is_not(None))
                .where(ScheduledTask.last_run_at < stale_cutoff)
            )
            or 0
        )
        status = (
            InfraHealthStatus.DEGRADED if stale_count else InfraHealthStatus.HEALTHY
        )
        return [
            HealthCheckResult(
                category=InfraHealthCategory.SCHEDULED_JOBS,
                check_key="scheduled_tasks",
                display_name="Scheduled Jobs",
                status=status,
                severity=InfraAlertSeverity.WARNING,
                summary=f"{enabled_count} enabled scheduled job(s); {stale_count} stale",
                details={
                    "enabled_count": int(enabled_count),
                    "stale_count": int(stale_count),
                },
            )
        ]

    def _external_dependency_checks(self) -> list[HealthCheckResult]:
        checks: list[HealthCheckResult] = []
        try:
            dependencies = collect_dependency_health()
        except Exception as exc:
            return [
                HealthCheckResult(
                    category=InfraHealthCategory.EXTERNAL,
                    check_key="dependency_health",
                    display_name="External Dependencies",
                    status=InfraHealthStatus.UNKNOWN,
                    severity=InfraAlertSeverity.WARNING,
                    summary="Could not collect external dependency health",
                    details={"error": str(exc)[:240]},
                )
            ]
        for name, payload in dependencies.items():
            required = bool(payload.get("required"))
            healthy = bool(payload.get("healthy"))
            configured = bool(payload.get("configured"))
            if healthy or (not configured and not required):
                status = InfraHealthStatus.HEALTHY
                severity = InfraAlertSeverity.INFO
            elif required:
                status = InfraHealthStatus.UNHEALTHY
                severity = InfraAlertSeverity.CRITICAL
            else:
                status = InfraHealthStatus.DEGRADED
                severity = InfraAlertSeverity.WARNING
            checks.append(
                HealthCheckResult(
                    category=InfraHealthCategory.EXTERNAL,
                    check_key=name,
                    display_name=name.replace("_", " ").title(),
                    status=status,
                    severity=severity,
                    summary=str(
                        payload.get("message") or payload.get("status") or name
                    ),
                    details=dict(payload),
                    last_activity_at=datetime.now(UTC) if healthy else None,
                )
            )
        return checks

    def _upsert_status(
        self, db: Session, result: HealthCheckResult, now: datetime
    ) -> InfrastructureHealthStatus:
        details = self._json_safe_details(result.details)
        row = db.scalar(
            select(InfrastructureHealthStatus)
            .where(InfrastructureHealthStatus.category == result.category)
            .where(InfrastructureHealthStatus.check_key == result.check_key)
        )
        if row is None:
            row = InfrastructureHealthStatus(
                category=result.category,
                check_key=result.check_key,
                display_name=result.display_name,
                status=result.status,
                severity=result.severity,
                summary=result.summary,
                details=details,
                last_checked_at=now,
                last_activity_at=result.last_activity_at,
            )
            db.add(row)
        else:
            row.display_name = result.display_name
            row.status = result.status
            row.severity = result.severity
            row.summary = result.summary
            row.details = details
            row.last_checked_at = now
            row.last_activity_at = result.last_activity_at or row.last_activity_at
        if result.status == InfraHealthStatus.HEALTHY:
            row.last_healthy_at = now
        elif result.is_problem:
            row.last_unhealthy_at = now
        return row

    def _upsert_alert(
        self, db: Session, result: HealthCheckResult, now: datetime
    ) -> dict[str, Any] | None:
        details = self._json_safe_details(result.details)
        alert = db.scalar(
            select(InfrastructureAlert).where(
                InfrastructureAlert.fingerprint == result.fingerprint
            )
        )
        if alert is None:
            alert = InfrastructureAlert(
                fingerprint=result.fingerprint,
                category=result.category,
                check_key=result.check_key,
                title=result.display_name,
                summary=result.summary,
                severity=result.severity,
                status=InfraAlertStatus.OPEN,
                details=details,
                first_seen_at=now,
                last_seen_at=now,
                occurrence_count=1,
            )
            db.add(alert)
            db.flush()
            return {
                "event": "created",
                "alert_id": alert.id,
                "title": alert.title,
                "summary": alert.summary,
                "prefix": "New infrastructure alert",
            }

        previous_status = alert.status
        previous_severity = alert.severity
        alert.title = result.display_name
        alert.summary = result.summary
        alert.severity = result.severity
        alert.status = InfraAlertStatus.OPEN
        alert.details = details
        alert.last_seen_at = now
        alert.resolved_at = None
        alert.occurrence_count += 1
        db.flush()

        if previous_status == InfraAlertStatus.RESOLVED:
            return {
                "event": "reopened",
                "alert_id": alert.id,
                "title": alert.title,
                "summary": alert.summary,
                "prefix": "Infrastructure alert reopened",
            }
        if self._severity_rank(result.severity) > self._severity_rank(
            previous_severity
        ):
            return {
                "event": "escalated",
                "alert_id": alert.id,
                "title": alert.title,
                "summary": alert.summary,
                "prefix": "Infrastructure alert escalated",
            }
        return None

    def _resolve_alert(self, db: Session, fingerprint: str, now: datetime) -> bool:
        alert = db.scalar(
            select(InfrastructureAlert)
            .where(InfrastructureAlert.fingerprint == fingerprint)
            .where(InfrastructureAlert.status == InfraAlertStatus.OPEN)
        )
        if alert is None:
            return False
        alert.status = InfraAlertStatus.RESOLVED
        alert.resolved_at = now
        alert.last_seen_at = now
        return True

    def _resolve_missing_check_alerts(
        self, db: Session, seen_fingerprints: set[str], now: datetime
    ) -> None:
        open_alerts = list(
            db.scalars(
                select(InfrastructureAlert).where(
                    InfrastructureAlert.status == InfraAlertStatus.OPEN
                )
            )
        )
        for alert in open_alerts:
            if alert.fingerprint not in seen_fingerprints:
                alert.status = InfraAlertStatus.RESOLVED
                alert.resolved_at = now
                alert.last_seen_at = now

    def deliver_notifications(
        self, db: Session, events: list[AlertNotificationEvent]
    ) -> int:
        """Deliver every event to ONE tenant's monitoring users.

        ``db`` is a tenant-scoped session. An infrastructure alert is
        fleet-wide, but the people it notifies are not: recipients are
        ``Person`` rows and notifications are tenant rows, so the delivery
        phase runs once per organization. The loop over organizations lives in
        :func:`run_infrastructure_health_checks`, which owns session lifecycle.
        """
        delivered = 0
        for event in events:
            delivered += self._notify_monitoring_users(db, event)
        return delivered

    def _notify_monitoring_users(
        self, db: Session, event: AlertNotificationEvent
    ) -> int:
        recipients = self._monitoring_recipients(db)
        delivered = 0
        for person in recipients:
            if person.organization_id is None:
                continue
            try:
                notification_service.create(
                    db,
                    organization_id=person.organization_id,
                    recipient_id=person.id,
                    entity_type=EntityType.SYSTEM,
                    entity_id=event.alert_id,
                    notification_type=NotificationType.ALERT,
                    title=f"{event.prefix}: {event.title}",
                    message=event.summary,
                    channel=NotificationChannel.IN_APP,
                    action_url=f"/admin/system/health/alerts/{event.alert_id}",
                )
                delivered += 1
            except Exception:
                logger.exception(
                    "Infrastructure alert notification failed for recipient %s",
                    person.id,
                )
        if delivered:
            alert = db.get(InfrastructureAlert, event.alert_id)
            if alert is not None:
                alert.last_notification_at = datetime.now(UTC)
        return delivered

    def _monitoring_recipients(self, db: Session) -> list[Person]:
        """The monitoring users of the organization ``db`` is scoped to.

        This used to run under ``allow_cross_org`` to collect every tenant's
        monitoring users in one pass. That bypasses only the SQLAlchemy
        listener, never PostgreSQL RLS, so under ``app_user`` it returns zero
        recipients and every infrastructure alert is delivered to nobody while
        the health check still reports success.
        """
        recipient_ids = (
            select(Person.id)
            .join(PersonRole, PersonRole.person_id == Person.id)
            .join(Role, Role.id == PersonRole.role_id)
            .join(RolePermission, RolePermission.role_id == Role.id)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .where(Role.is_active.is_(True))
            .where(Permission.is_active.is_(True))
            .where(Permission.key.in_(MONITORING_READ_PERMISSIONS))
            .distinct()
        )
        return list(db.scalars(select(Person).where(Person.id.in_(recipient_ids))))

    def _worst_status(self, db: Session) -> InfraHealthStatus:
        statuses = list(db.scalars(select(InfrastructureHealthStatus.status)))
        if InfraHealthStatus.UNHEALTHY in statuses:
            return InfraHealthStatus.UNHEALTHY
        if InfraHealthStatus.DEGRADED in statuses:
            return InfraHealthStatus.DEGRADED
        if InfraHealthStatus.UNKNOWN in statuses:
            return InfraHealthStatus.UNKNOWN
        return InfraHealthStatus.HEALTHY

    def _severity_rank(self, severity: InfraAlertSeverity) -> int:
        return {
            InfraAlertSeverity.INFO: 1,
            InfraAlertSeverity.WARNING: 2,
            InfraAlertSeverity.CRITICAL: 3,
        }[severity]

    def _json_safe_details(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): self._json_safe_details(item) for key, item in value.items()
            }
        if isinstance(value, list | tuple | set):
            return [self._json_safe_details(item) for item in value]
        if isinstance(value, Decimal):
            return int(value) if value == value.to_integral_value() else float(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, timedelta):
            return value.total_seconds()
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, Path):
            return str(value)
        if value is None or isinstance(value, str | int | float | bool):
            return value
        return str(value)

    def _linux_memory_usage(self) -> dict[str, int | float] | None:
        try:
            values: dict[str, int] = {}
            with open("/proc/meminfo") as meminfo:
                for line in meminfo:
                    key, raw_value = line.split(":", 1)
                    values[key] = int(raw_value.strip().split()[0]) * 1024
            total = values.get("MemTotal")
            available = values.get("MemAvailable")
            if not total or available is None:
                return None
            used = max(total - available, 0)
            return {
                "total_bytes": total,
                "available_bytes": available,
                "used_bytes": used,
                "used_percent": round((used / total) * 100, 1),
            }
        except Exception:
            return None

    def _load_average(self) -> dict[str, float | int] | None:
        getloadavg = cast(
            Callable[[], tuple[float, float, float]] | None,
            getattr(os, "getloadavg", None),
        )
        if getloadavg is None:
            return None
        try:
            one, five, fifteen = getloadavg()
        except OSError:
            return None
        cpu_count = os.cpu_count() or 1
        return {
            "cpu_count": cpu_count,
            "one_minute": round(one, 2),
            "five_minutes": round(five, 2),
            "fifteen_minutes": round(fifteen, 2),
            "one_minute_per_cpu": round(one / cpu_count, 2),
        }

    def _linux_uptime_seconds(self) -> int | None:
        try:
            with open("/proc/uptime") as uptime_file:
                return int(float(uptime_file.read().split()[0]))
        except Exception:
            return None

    def _parse_category(self, value: str) -> InfraHealthCategory | None:
        try:
            return InfraHealthCategory(value) if value else None
        except ValueError:
            return None

    def _parse_severity(self, value: str) -> InfraAlertSeverity | None:
        try:
            return InfraAlertSeverity(value) if value else None
        except ValueError:
            return None

    def _parse_alert_status(self, value: str) -> InfraAlertStatus | None:
        try:
            return InfraAlertStatus(value) if value else None
        except ValueError:
            return None

    def _period_start(self, period: str) -> datetime | None:
        now = datetime.now(UTC)
        if period == "24h":
            return now - timedelta(hours=24)
        if period == "30d":
            return now - timedelta(days=30)
        if period == "all":
            return None
        return now - timedelta(days=7)


infrastructure_health_service = InfrastructureHealthService()


def _deliver_alerts_to_every_tenant(events: list[AlertNotificationEvent]) -> int:
    """Fan the fleet-wide alerts out over tenants, one session each.

    The checks themselves read fleet tables (``infrastructure_health_status``,
    ``infrastructure_alert`` — neither carries an ``organization_id``), so they
    run once on the unscoped session. Delivery is the opposite: recipients are
    ``Person`` rows and notifications are tenant rows, both RLS-protected, so
    each organization is served inside its own tenant session obtained from the
    catalogue definer.

    ``include_inactive=True`` preserves the previous recipient set exactly: the
    cross-org scan it replaces never looked at organization status. Narrowing
    infrastructure alerting to active tenants is a product decision, not part
    of this cutover.

    One tenant's failure is contained to that tenant — the remaining
    organizations are still notified, and the health-check result is still
    returned.
    """
    delivered = 0
    for organization_id, db in for_each_organization(include_inactive=True):
        try:
            count = infrastructure_health_service.deliver_notifications(db, events)
            if count:
                db.commit()
                delivered += count
        except Exception:
            db.rollback()
            logger.exception(
                "Infrastructure alert notification delivery failed for organization %s",
                organization_id,
            )
    return delivered


def run_infrastructure_health_checks() -> dict[str, Any]:
    db = SessionLocal()
    try:
        result = infrastructure_health_service.run_checks(db)
        notification_events = list(result.pop("notification_events", []))
        db.commit()
        if notification_events:
            result["notifications"] = _deliver_alerts_to_every_tenant(
                notification_events
            )
        return result
    except Exception:
        db.rollback()
        logger.exception("Infrastructure health check failed")
        raise
    finally:
        db.close()
