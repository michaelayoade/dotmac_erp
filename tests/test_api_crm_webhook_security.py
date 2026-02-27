import asyncio
import hashlib
import hmac

import pytest
from fastapi import HTTPException

from app.api import crm as crm_api


class _FakeRequest:
    async def body(self) -> bytes:
        return b"{}"

    async def json(self) -> dict[str, str]:
        return {}


def test_verify_crm_signature_returns_false_when_secret_not_configured(monkeypatch):
    monkeypatch.setattr(crm_api.settings, "crm_webhook_secret", None, raising=False)

    assert crm_api.verify_crm_signature(b'{"event":"ticket.created"}', "any-value") is (
        False
    )


def test_verify_crm_signature_validates_hmac_sha256(monkeypatch):
    payload = b'{"event":"ticket.updated","id":"crm-123"}'
    secret = "webhook-secret"
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    monkeypatch.setattr(
        crm_api.settings,
        "crm_webhook_secret",
        secret,
        raising=False,
    )

    assert crm_api.verify_crm_signature(payload, signature) is True
    assert crm_api.verify_crm_signature(payload, "invalid") is False


def test_crm_webhook_returns_503_when_secret_not_configured(monkeypatch):
    monkeypatch.setattr(crm_api.settings, "crm_webhook_secret", None, raising=False)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            crm_api.crm_webhook(
                request=_FakeRequest(),
                x_crm_signature="any-signature",
                db=None,
            )
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "CRM webhook authentication is not configured"
