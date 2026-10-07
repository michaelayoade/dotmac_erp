from __future__ import annotations

import logging

import httpx
import pytest

from app.services.finance.payments.paystack_client import (
    PaystackClient,
    PaystackConfig,
    PaystackUnreachable,
    _safe_error_detail,
)


def test_safe_error_detail_drops_non_json_provider_body():
    secret_body = "<html>Cloudflare Your IP: 149.102.158.167</html>"
    response = httpx.Response(
        504,
        text=secret_body,
        request=httpx.Request("GET", "https://api.paystack.co/settlement"),
    )

    assert _safe_error_detail(response) == "HTTP 504"
    assert secret_body not in _safe_error_detail(response)


def test_safe_error_detail_keeps_bounded_paystack_message_and_code():
    response = httpx.Response(
        400,
        json={
            "status": False,
            "message": "Invalid key",
            "code": "invalid_key",
            "data": {"secret": "must not be logged"},
        },
        request=httpx.Request("GET", "https://api.paystack.co/settlement"),
    )

    detail = _safe_error_detail(response)

    assert detail == "HTTP 400: Invalid key; code=invalid_key"
    assert "must not be logged" not in detail


def test_list_settlements_does_not_log_or_raise_raw_provider_body(caplog):
    secret_body = "<html>Cloudflare Ray ID: secret-provider-detail</html>"
    response = httpx.Response(
        504,
        text=secret_body,
        request=httpx.Request("GET", "https://api.paystack.co/settlement"),
    )
    client = PaystackClient(PaystackConfig("secret", "public", "webhook"))

    with (
        caplog.at_level(
            logging.ERROR,
            logger="app.services.finance.payments.paystack_client",
        ),
        pytest.raises(PaystackUnreachable) as exc_info,
    ):
        client._client = _ResponseClient(response)
        client.list_settlements()

    assert "HTTP 504" in caplog.text
    assert secret_body not in caplog.text
    assert secret_body not in str(exc_info.value)


class _ResponseClient:
    def __init__(self, response: httpx.Response):
        self.response = response

    def request(self, **_kwargs):
        return self.response
