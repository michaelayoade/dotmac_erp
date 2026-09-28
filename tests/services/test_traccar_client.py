import logging

import pytest

from app.services.fleet.tracking.traccar_client import (
    TraccarClient,
    TraccarConfig,
    TraccarTransportResponse,
    TraccarUnavailableError,
)


def _config() -> TraccarConfig:
    return TraccarConfig(
        base_url="https://tracking.internal.example",
        username="erp-service",
        password="not-for-logs",
        connect_timeout=2.5,
        read_timeout=8.0,
    )


def test_unavailable_client_fails_closed_without_transport():
    with pytest.raises(TraccarUnavailableError, match="not enabled"):
        TraccarClient(_config()).request("GET", "/api/devices")


def test_client_supplies_auth_and_separate_timeouts_without_logging_secrets(caplog):
    captured = {}

    class FakeTransport:
        def request(self, **kwargs):
            captured.update(kwargs)
            return TraccarTransportResponse(200, [{"id": 42}])

    caplog.set_level(logging.DEBUG)
    result = TraccarClient(_config(), transport=FakeTransport()).request(
        "GET", "/api/devices"
    )

    assert result == [{"id": 42}]
    assert captured["connect_timeout"] == 2.5
    assert captured["read_timeout"] == 8.0
    assert captured["headers"]["Authorization"].startswith("Basic ")
    assert "not-for-logs" not in caplog.text
    assert "erp-service" not in caplog.text


def test_transport_timeout_is_a_structured_unavailable_error():
    class TimeoutTransport:
        def request(self, **kwargs):
            raise TimeoutError("provider timed out")

    with pytest.raises(TraccarUnavailableError, match="unavailable"):
        TraccarClient(_config(), transport=TimeoutTransport()).request(
            "GET", "/api/devices"
        )
