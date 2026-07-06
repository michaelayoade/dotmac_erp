"""A transient webhook-processing failure returns 503 so dotmac_sub retries it.

dotmac_sub's WebhookDelivery bounds retries with backoff and dead-letters on
exhaustion (no storm), and this handler is idempotent — so surfacing a 5xx is
strictly better than silently swallowing the failure as 200 (which left recovery
to the next full-sync poll).
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from fastapi import HTTPException

from app.api import dotmac_sub as ds


class _FakeRequest:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def body(self) -> bytes:
        return self._payload

    async def json(self) -> dict:
        return json.loads(self._payload.decode("utf-8"))


def _call(payload: bytes):
    return asyncio.run(
        ds.dotmac_sub_webhook(
            request=_FakeRequest(payload),
            x_webhook_signature_256="sha256=stub",
            x_dotmacsub_signature=None,
            db=None,
        )
    )


def _configure(monkeypatch):
    monkeypatch.setattr(ds.settings, "dotmac_sub_webhook_secret", "s", raising=False)
    monkeypatch.setattr(
        ds.settings, "default_organization_id", str(uuid.uuid4()), raising=False
    )
    monkeypatch.setattr(ds, "verify_dotmac_sub_signature", lambda body, sig: True)
    monkeypatch.setattr(ds, "prime_tenant_context", lambda db, org: None)


def test_processing_error_returns_503(monkeypatch):
    _configure(monkeypatch)
    import app.services.dotmac_sub.webhook_dispatch as wd

    def _boom(*a, **k):
        raise RuntimeError("transient GL resolution failure")

    monkeypatch.setattr(wd, "dispatch_webhook", _boom)

    with pytest.raises(HTTPException) as exc:
        _call(json.dumps({"event_type": "payment.received"}).encode())
    assert exc.value.status_code == 503


def test_successful_dispatch_returns_ok(monkeypatch):
    _configure(monkeypatch)
    import app.services.dotmac_sub.webhook_dispatch as wd

    monkeypatch.setattr(wd, "dispatch_webhook", lambda *a, **k: {"status": "ok"})

    resp = _call(json.dumps({"event_type": "payment.received"}).encode())
    assert resp.status == "ok"
