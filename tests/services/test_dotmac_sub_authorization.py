"""Authorization-denial behavior for the Dotmac Sub integration."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.services.dotmac_sub import client as client_module
from app.services.dotmac_sub.client import (
    DotmacSubAuthorizationError,
    DotmacSubClient,
    DotmacSubConfig,
)


def test_generic_forbidden_response_is_an_authorization_denial() -> None:
    client = DotmacSubClient(DotmacSubConfig(api_url="https://x", api_token="svc-key"))
    response = httpx.Response(403, json={"detail": "forbidden"})

    with pytest.raises(DotmacSubAuthorizationError, match="/subscribers"):
        client._handle_response(response, endpoint="/subscribers")


def test_authorization_denial_has_its_own_request_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[str, str, str]] = []
    client = DotmacSubClient(DotmacSubConfig(api_url="https://x", api_token="svc-key"))
    client._engine.request = MagicMock(
        side_effect=DotmacSubAuthorizationError("denied", status_code=403)
    )
    monkeypatch.setattr(
        client_module,
        "observe_integration_request",
        lambda integration, operation, status, _duration: observed.append(
            (integration, operation, status)
        ),
    )

    with pytest.raises(DotmacSubAuthorizationError):
        client._request("GET", "/subscribers")

    assert observed == [("dotmac_sub", "GET /subscribers", "authorization_denied")]
