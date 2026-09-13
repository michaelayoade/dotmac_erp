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
    DotmacSubPermanentSyncError,
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


def test_talk_mapping_forbidden_names_required_machine_scope() -> None:
    client = DotmacSubClient(DotmacSubConfig(api_url="https://x", api_token="key"))
    response = httpx.Response(403, json={"detail": "forbidden"})

    with pytest.raises(
        DotmacSubPermanentSyncError,
        match="communications:nextcloud_talk_staff:manage",
    ):
        client._handle_response(
            response,
            endpoint="/staff-accounts/account-1/nextcloud-talk",
        )


def test_staff_create_and_talk_calls_carry_stable_idempotency_headers() -> None:
    client = DotmacSubClient(DotmacSubConfig(api_url="https://x", api_token="key"))
    client._engine.request = MagicMock(return_value={"id": "account-1"})

    client.create_staff_account(
        email="person@dotmac.ng",
        first_name="Test",
        last_name="Person",
        roles=["staff"],
        idempotency_key="erp-create-1",
    )
    client.set_staff_account_nextcloud_talk(
        "account-1",
        nextcloud_user_id="person@dotmac.ng",
        idempotency_key="erp-talk-1",
    )
    client.disable_staff_account_nextcloud_talk(
        "account-1",
        idempotency_key="erp-talk-disable-1",
    )

    create_call, map_call, disable_call = client._engine.request.call_args_list
    assert create_call.kwargs["json_data"]["existing_account_policy"] == "reject"
    assert create_call.kwargs["headers"] == {"Idempotency-Key": "erp-create-1"}
    assert map_call.args[:2] == (
        "PUT",
        "/staff-accounts/account-1/nextcloud-talk",
    )
    assert map_call.kwargs["headers"] == {"Idempotency-Key": "erp-talk-1"}
    assert disable_call.args[:2] == (
        "POST",
        "/staff-accounts/account-1/nextcloud-talk/disable",
    )
    assert disable_call.kwargs["headers"] == {"Idempotency-Key": "erp-talk-disable-1"}
