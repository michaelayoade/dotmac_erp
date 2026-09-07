from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from app.services.dotmac_sub.client import (
    DotmacSubClient,
    DotmacSubConfig,
    DotmacSubError,
)


def _body(reference: str) -> bytes:
    return json.dumps({"data": {"reference": reference}}).encode()


def _signature(body: bytes) -> str:
    return hmac.new(b"test-secret", body, hashlib.sha512).hexdigest()


def test_relay_retries_transient_server_error_with_exact_body(monkeypatch) -> None:
    body = _body("DMAC-SELFCARE-RETRY")
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        assert request.content == body
        assert request.headers["X-Paystack-Signature"] == _signature(body)
        if attempts == 1:
            return httpx.Response(503, json={"status": "unavailable"})
        return httpx.Response(200, json={"status": "processed"})

    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    client = DotmacSubClient(
        DotmacSubConfig(
            api_url="https://selfcare.example", api_token="key", max_retries=3
        )
    )
    client._client = httpx.Client(
        base_url="https://selfcare.example/api/v1",
        transport=httpx.MockTransport(handler),
    )

    assert client.relay_paystack_webhook(
        raw_payload=body,
        signature=_signature(body),
    ) == {"status": "processed"}
    assert attempts == 2


def test_relay_circuit_opens_after_transport_failure(monkeypatch) -> None:
    body = _body("DMAC-SELFCARE-CIRCUIT")
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("slow Self-Care response")

    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    client = DotmacSubClient(
        DotmacSubConfig(
            api_url="https://selfcare.example", api_token="key", max_retries=2
        )
    )
    client._client = httpx.Client(
        base_url="https://selfcare.example/api/v1",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(DotmacSubError, match="failed after 2 attempts"):
        client.relay_paystack_webhook(
            raw_payload=body,
            signature=_signature(body),
        )
    with pytest.raises(DotmacSubError, match="circuit open"):
        client.relay_paystack_webhook(
            raw_payload=body,
            signature=_signature(body),
        )
    assert attempts == 2
