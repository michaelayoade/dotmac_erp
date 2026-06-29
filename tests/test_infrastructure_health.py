import uuid
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.infrastructure_health import (
    InfraAlertSeverity,
    InfraAlertStatus,
    InfraHealthCategory,
    InfraHealthStatus,
    InfrastructureAlert,
    InfrastructureHealthStatus,
)
from app.models.notification import Notification
from app.models.person import Person
from app.models.rbac import Permission, PersonRole, Role, RolePermission
from app.services.admin.web import admin_web_service
from app.services.infrastructure_health import (
    HealthCheckResult,
    InfrastructureHealthService,
)
from app.web.deps import WebAuthContext
from app.web.admin import router as admin_router


def _result(
    *,
    status: InfraHealthStatus,
    severity: InfraAlertSeverity = InfraAlertSeverity.WARNING,
    summary: str = "Redis is unhealthy",
) -> HealthCheckResult:
    return HealthCheckResult(
        category=InfraHealthCategory.CACHE,
        check_key="redis",
        display_name="Redis Cache",
        status=status,
        severity=severity,
        summary=summary,
        details={"source": "test"},
    )


def test_infrastructure_alert_create_dedupe_and_resolve(db_session):
    service = InfrastructureHealthService()

    service.collect_checks = lambda db: [_result(status=InfraHealthStatus.UNHEALTHY)]  # type: ignore[method-assign]
    first = service.run_checks(db_session)
    second = service.run_checks(db_session)

    alerts = list(db_session.scalars(select(InfrastructureAlert)))
    assert first["created"] == 1
    assert len(first["notification_events"]) == 1
    assert second["created"] == 0
    assert second["notification_events"] == []
    assert len(alerts) == 1
    assert alerts[0].status == InfraAlertStatus.OPEN
    assert alerts[0].occurrence_count == 2

    service.collect_checks = lambda db: [_result(status=InfraHealthStatus.HEALTHY)]  # type: ignore[method-assign]
    resolved = service.run_checks(db_session)
    db_session.refresh(alerts[0])

    assert resolved["resolved"] == 1
    assert alerts[0].status == InfraAlertStatus.RESOLVED
    assert alerts[0].resolved_at is not None


def test_infrastructure_health_details_are_json_safe(db_session):
    service = InfrastructureHealthService()
    service.collect_checks = lambda db: [  # type: ignore[method-assign]
        HealthCheckResult(
            category=InfraHealthCategory.REPLICATION,
            check_key="physical_streaming_replication",
            display_name="Physical Streaming Replication",
            status=InfraHealthStatus.DEGRADED,
            severity=InfraAlertSeverity.WARNING,
            summary="Replication lag detected",
            details={
                "bytes_behind": Decimal("1024"),
                "standbys": [{"replay_lag_seconds": Decimal("1.25")}],
            },
        )
    ]

    service.run_checks(db_session)
    db_session.flush()

    status = db_session.scalar(select(InfrastructureHealthStatus))
    alert = db_session.scalar(select(InfrastructureAlert))

    assert status is not None
    assert status.details["bytes_behind"] == 1024
    assert status.details["standbys"][0]["replay_lag_seconds"] == 1.25
    assert alert is not None
    assert alert.details["bytes_behind"] == 1024


def test_server_health_category_is_collected():
    service = InfrastructureHealthService()

    checks = service._server_checks()

    assert len(checks) == 1
    assert checks[0].category == InfraHealthCategory.SERVER
    assert checks[0].check_key == "server_resources"
    assert "disk" in checks[0].details or "disk_error" in checks[0].details
    assert service.category_label(InfraHealthCategory.SERVER) == "Server Health"


@pytest.mark.parametrize(
    ("loki_status", "expected_status", "expected_summary"),
    [
        (
            {"enabled": False, "consecutive_failures": 0},
            InfraHealthStatus.HEALTHY,
            "Loki logging disabled",
        ),
        (
            {"enabled": True, "consecutive_failures": 0},
            InfraHealthStatus.HEALTHY,
            "Loki logging healthy; 0 consecutive failure(s)",
        ),
        (
            {"enabled": True, "consecutive_failures": 5},
            InfraHealthStatus.DEGRADED,
            "Loki logging degraded; 5 consecutive failure(s)",
        ),
    ],
)
def test_application_loki_health_uses_monitoring_status_fields(
    monkeypatch, loki_status, expected_status, expected_summary
):
    service = InfrastructureHealthService()
    monkeypatch.setattr(
        "app.services.infrastructure_health.get_monitoring_status",
        lambda: {"loki": loki_status, "sentry": {"enabled": False}},
    )
    monkeypatch.setattr(
        "app.services.infrastructure_health.get_otel_status",
        lambda: {"enabled": False},
    )

    checks = service._application_checks()
    loki_check = next(check for check in checks if check.check_key == "loki_logging")

    assert loki_check.status == expected_status
    assert loki_check.summary == expected_summary
    assert loki_check.details == loki_status


def test_infrastructure_alert_reopen_and_escalate(db_session):
    service = InfrastructureHealthService()

    service.collect_checks = lambda db: [_result(status=InfraHealthStatus.DEGRADED)]  # type: ignore[method-assign]
    service.run_checks(db_session)
    service.collect_checks = lambda db: [_result(status=InfraHealthStatus.HEALTHY)]  # type: ignore[method-assign]
    service.run_checks(db_session)

    service.collect_checks = lambda db: [  # type: ignore[method-assign]
        _result(
            status=InfraHealthStatus.UNHEALTHY,
            severity=InfraAlertSeverity.CRITICAL,
            summary="Redis is down",
        )
    ]
    reopened = service.run_checks(db_session)
    escalated = service.run_checks(db_session)
    alert = db_session.scalar(select(InfrastructureAlert))

    assert reopened["reopened"] == 1
    assert len(reopened["notification_events"]) == 1
    assert escalated["escalated"] == 0
    assert escalated["notification_events"] == []
    assert alert is not None
    assert alert.status == InfraAlertStatus.OPEN
    assert alert.severity == InfraAlertSeverity.CRITICAL


def test_infrastructure_alert_notifications_target_monitoring_users(
    db_session, person, monkeypatch
):
    service = InfrastructureHealthService()
    allow_cross_org_calls = 0

    @contextmanager
    def fake_allow_cross_org(db):
        nonlocal allow_cross_org_calls
        allow_cross_org_calls += 1
        yield

    monkeypatch.setattr(
        "app.services.infrastructure_health.allow_cross_org", fake_allow_cross_org
    )
    role = Role(name=f"monitoring_{uuid.uuid4().hex}", is_active=True)
    permission = Permission(
        key="system:alerts:read",
        description="View infrastructure alerts",
        is_active=True,
    )
    non_monitoring = Person(
        first_name="Regular",
        last_name="User",
        email=f"regular-{uuid.uuid4().hex}@example.com",
        organization_id=person.organization_id,
    )
    db_session.add_all([role, permission, non_monitoring])
    db_session.flush()
    db_session.add_all(
        [
            PersonRole(person_id=person.id, role_id=role.id),
            RolePermission(role_id=role.id, permission_id=permission.id),
        ]
    )
    db_session.flush()

    service.collect_checks = lambda db: [  # type: ignore[method-assign]
        _result(status=InfraHealthStatus.UNHEALTHY)
    ]
    result = service.run_checks(db_session)
    db_session.commit()
    delivered = service.deliver_notifications(db_session, result["notification_events"])

    notifications = list(db_session.scalars(select(Notification)))
    assert delivered == 1
    assert len(notifications) == 1
    assert notifications[0].recipient_id == person.id
    assert notifications[0].action_url.startswith("/admin/system/health/alerts/")
    assert allow_cross_org_calls == 1


def test_infrastructure_health_persists_when_notification_delivery_fails(
    db_session, person, monkeypatch
):
    service = InfrastructureHealthService()
    service.collect_checks = lambda db: [  # type: ignore[method-assign]
        _result(status=InfraHealthStatus.UNHEALTHY)
    ]

    result = service.run_checks(db_session)
    db_session.commit()

    monkeypatch.setattr(service, "_monitoring_recipients", lambda db: [person])

    def fail_create(*args, **kwargs):
        raise RuntimeError("notification unavailable")

    monkeypatch.setattr(
        "app.services.infrastructure_health.notification_service.create", fail_create
    )
    delivered = service.deliver_notifications(db_session, result["notification_events"])
    db_session.rollback()

    statuses = list(db_session.scalars(select(InfrastructureHealthStatus)))
    alerts = list(db_session.scalars(select(InfrastructureAlert)))

    assert delivered == 0
    assert len(statuses) == 1
    assert statuses[0].check_key == "redis"
    assert len(alerts) == 1
    assert alerts[0].status == InfraAlertStatus.OPEN


def test_system_monitoring_auth_uses_permissions_not_roles():
    auth = WebAuthContext(
        is_authenticated=True,
        person_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        roles=["custom_monitor"],
        scopes=["system:health:read"],
    )
    request = type(
        "RequestStub",
        (),
        {"url": type("UrlStub", (), {"path": "/admin/system/health", "query": ""})()},
    )()

    assert admin_web_service._require_system_monitoring_auth(request, auth) is auth


def test_system_monitoring_auth_rejects_without_permission():
    auth = WebAuthContext(
        is_authenticated=True,
        person_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        roles=["custom_user"],
        scopes=["settings:read"],
    )
    request = type(
        "RequestStub",
        (),
        {"url": type("UrlStub", (), {"path": "/admin/system/health", "query": ""})()},
    )()

    with pytest.raises(HTTPException) as exc:
        admin_web_service._require_system_monitoring_auth(request, auth)

    assert exc.value.status_code == 403


def test_infrastructure_alert_routes_live_under_system_health():
    paths = {getattr(route, "path", "") for route in admin_router.routes}

    assert "/admin/system/health/alerts" in paths
    assert "/admin/system/health/alerts/{alert_id}" in paths
    assert "/admin/system/alerts" not in paths
    assert "/admin/system/alerts/{alert_id}" not in paths


def test_system_navigation_links_alerts_under_health():
    nav = Path("templates/admin/base_admin.html").read_text()
    health = Path("templates/admin/system/health.html").read_text()
    alerts = Path("templates/admin/system/alerts.html").read_text()
    detail = Path("templates/admin/system/alert_detail.html").read_text()
    dashboard = Path("templates/admin/dashboard.html").read_text()

    assert "Infrastructure Alerts</span>" not in nav
    assert 'href="/admin/system/alerts"' not in nav
    assert 'href="/admin/system/health/alerts"' in health
    assert "/admin/system/health/alerts/{{ alert.id }}" in alerts
    assert "/admin/system/health/alerts/{{ item.id }}" in detail
    assert "/admin/system/health/alerts/{{ alert.id }}" in dashboard
