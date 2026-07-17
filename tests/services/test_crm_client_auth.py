"""erp→crm outbound auth: scoped X-API-Key preferred over the legacy bearer."""

from __future__ import annotations

from app.services.crm.client import CRMClient, CRMConfig


def _headers(config: CRMConfig) -> dict:
    client = CRMClient(config)
    try:
        return dict(client.client.headers)
    finally:
        client.close()


def test_api_key_preferred():
    headers = _headers(
        CRMConfig(url="http://crm", api_token="legacy", api_key="svc-key")
    )
    assert headers["x-api-key"] == "svc-key"
    assert "authorization" not in headers


def test_bearer_fallback_when_no_key():
    headers = _headers(CRMConfig(url="http://crm", api_token="legacy"))
    assert headers["authorization"] == "Bearer legacy"
    assert "x-api-key" not in headers


def test_no_credentials_sends_no_auth_headers():
    headers = _headers(CRMConfig(url="http://crm"))
    assert "authorization" not in headers
    assert "x-api-key" not in headers
