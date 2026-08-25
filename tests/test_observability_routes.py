import json

import app.dependency_health as dependency_health_module
import app.main as main_module
import app.monitoring as monitoring_module
import app.telemetry as telemetry_module
from starlette.requests import Request


def _make_request(
    *,
    headers: dict[str, str] | None = None,
    client_host: str = "testclient",
) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/health/monitoring",
            "raw_path": b"/health/monitoring",
            "query_string": b"",
            "headers": [
                (key.lower().encode(), value.encode())
                for key, value in (headers or {}).items()
            ],
            "client": (client_host, 1234),
            "server": ("testserver", 80),
        }
    )


def test_metrics_authorized_accepts_matching_token(monkeypatch) -> None:
    monkeypatch.setenv("METRICS_TOKEN", "metrics-secret")

    request = _make_request(headers={"x-metrics-token": "metrics-secret"})

    assert main_module._metrics_authorized(request) is True


def test_metrics_authorized_accepts_bearer_token(monkeypatch) -> None:
    """Fleet-standard auth: Authorization: Bearer <token>."""
    monkeypatch.setenv("METRICS_TOKEN", "metrics-secret")

    request = _make_request(headers={"Authorization": "Bearer metrics-secret"})

    assert main_module._metrics_authorized(request) is True


def test_metrics_authorized_rejects_wrong_bearer_token(monkeypatch) -> None:
    monkeypatch.setenv("METRICS_TOKEN", "metrics-secret")

    request = _make_request(headers={"Authorization": "Bearer nope"})

    assert main_module._metrics_authorized(request) is False


def test_monitoring_health_requires_auth(monkeypatch) -> None:
    """Unauthorized observability endpoints answer 404, not 403: on a publicly
    reachable host the endpoint should be indistinguishable from absent."""
    monkeypatch.setenv("MONITORING_TOKEN", "monitor-secret")

    response = main_module.monitoring_health(_make_request())

    assert response.status_code == 404


def test_monitoring_health_returns_503_when_sentry_transport_is_unhealthy(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MONITORING_TOKEN", "monitor-secret")
    monkeypatch.setattr(
        monitoring_module,
        "get_monitoring_status",
        lambda: {
            "loki": {
                "enabled": True,
                "url": "https://loki.internal/loki/api/v1/push",
                "sent": 10,
                "dropped": 1,
                "last_error": "",
                "last_success_ts": 123.0,
                "consecutive_failures": 0,
            },
            "sentry": {
                "enabled": True,
                "dsn_configured": True,
                "transport_healthy": False,
            },
        },
    )
    monkeypatch.setattr(
        telemetry_module,
        "get_otel_status",
        lambda: {
            "enabled": False,
            "exporter_configured": False,
            "initialized": False,
            "service_name": "dotmac_erp",
            "scope": "process",
        },
    )

    response = main_module.monitoring_health(
        _make_request(headers={"x-metrics-token": "monitor-secret"})
    )

    assert response.status_code == 503
    payload = json.loads(response.body.decode())
    assert payload["status"] == "degraded"


def test_monitoring_health_returns_503_when_tracing_is_enabled_but_not_initialized(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MONITORING_TOKEN", "monitor-secret")
    monkeypatch.setattr(
        monitoring_module,
        "get_monitoring_status",
        lambda: {
            "loki": {
                "enabled": False,
                "url": "",
                "sent": 0,
                "dropped": 0,
                "last_error": "",
                "last_success_ts": 0.0,
                "consecutive_failures": 0,
            },
            "sentry": {
                "enabled": False,
                "dsn_configured": False,
                "transport_healthy": False,
            },
        },
    )
    monkeypatch.setattr(
        telemetry_module,
        "get_otel_status",
        lambda: {
            "enabled": True,
            "exporter_configured": True,
            "initialized": False,
            "service_name": "dotmac_erp",
            "scope": "process",
        },
    )

    response = main_module.monitoring_health(
        _make_request(headers={"x-metrics-token": "monitor-secret"})
    )

    assert response.status_code == 503
    payload = json.loads(response.body.decode())
    assert payload["integrations"]["tracing"]["enabled"] is True


def test_readiness_probe_reports_degraded_optional_dependencies(monkeypatch) -> None:
    monkeypatch.setattr(
        main_module,
        "_check_database",
        lambda: {"healthy": True, "message": "Connected"},
    )
    monkeypatch.setattr(
        main_module,
        "_check_redis",
        lambda: {"healthy": True, "message": "Connected"},
    )
    monkeypatch.setattr(
        dependency_health_module,
        "collect_dependency_health",
        lambda: {
            "dotmac_sub": {
                "configured": True,
                "healthy": False,
                "required": False,
                "status": "degraded",
                "message": "Sub timeout",
            }
        },
    )
    monkeypatch.setattr(
        dependency_health_module,
        "readiness_failures",
        lambda dependencies: {},
    )

    payload = main_module.readiness_probe()

    assert payload["status"] == "ready_with_degraded_dependencies"
    assert payload["dependencies"]["dotmac_sub"]["healthy"] is False


def test_readiness_probe_returns_503_for_required_dependency_failures(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        main_module,
        "_check_database",
        lambda: {"healthy": True, "message": "Connected"},
    )
    monkeypatch.setattr(
        main_module,
        "_check_redis",
        lambda: {"healthy": True, "message": "Connected"},
    )
    dependency_payload = {
        "storage": {
            "configured": True,
            "healthy": False,
            "required": True,
            "status": "degraded",
            "message": "Bucket missing",
        }
    }
    monkeypatch.setattr(
        dependency_health_module,
        "collect_dependency_health",
        lambda: dependency_payload,
    )
    monkeypatch.setattr(
        dependency_health_module,
        "readiness_failures",
        lambda dependencies: dependencies,
    )

    response = main_module.readiness_probe()

    assert response.status_code == 503
    payload = json.loads(response.body.decode())
    assert payload["status"] == "not_ready"
    assert payload["dependencies"]["storage"]["required"] is True
