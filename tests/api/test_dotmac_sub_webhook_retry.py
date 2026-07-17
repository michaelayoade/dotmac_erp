"""The webhook endpoint ACKs fast and processes asynchronously.

The synchronous dispatch read back into dotmac_sub, whose rate limiter
throttles bursts (observed ~94% 503s in prod). The endpoint now enqueues a
Celery task (which paces reads via rate_limit and retries locally on
DotmacSubRateLimitError with backoff) and dedupes on dotmac_sub's
X-Webhook-Delivery-Id, since the sender retries the same delivery id. 503 is
reserved for enqueue failure, where the sender's bounded retry redelivers.
Retry exhaustion is safe: the scheduled incremental sync is the backstop and
the handler is idempotent.
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


def _call(payload: bytes, delivery_id: str | None = None):
    return asyncio.run(
        ds.dotmac_sub_webhook(
            request=_FakeRequest(payload),
            x_webhook_signature_256="sha256=stub",
            x_dotmacsub_signature=None,
            x_webhook_delivery_id=delivery_id,
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


def test_valid_webhook_is_accepted_and_enqueued(monkeypatch):
    _configure(monkeypatch)
    import app.tasks.dotmac_sub as tasks

    enqueued: list[tuple] = []
    monkeypatch.setattr(
        tasks.process_dotmac_sub_webhook,
        "delay",
        lambda *a, **k: enqueued.append((a, k)),
    )

    resp = _call(json.dumps({"event_type": "payment.received"}).encode())
    assert resp.status == "accepted"
    assert len(enqueued) == 1
    org_id, event_type, payload = enqueued[0][0]
    assert event_type == "payment.received"


def test_enqueue_failure_returns_503_for_sender_retry(monkeypatch):
    _configure(monkeypatch)
    import app.tasks.dotmac_sub as tasks

    def _boom(*a, **k):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(tasks.process_dotmac_sub_webhook, "delay", _boom)

    with pytest.raises(HTTPException) as exc:
        _call(json.dumps({"event_type": "payment.received"}).encode())
    assert exc.value.status_code == 503


def test_duplicate_delivery_id_is_acknowledged_without_enqueue(monkeypatch):
    _configure(monkeypatch)
    import app.tasks.dotmac_sub as tasks

    enqueued: list[tuple] = []
    monkeypatch.setattr(
        tasks.process_dotmac_sub_webhook,
        "delay",
        lambda *a, **k: enqueued.append((a, k)),
    )
    # The first delivery is unseen (enqueues, then records); the replay is seen.
    monkeypatch.setattr(
        ds, "_webhook_delivery_seen", lambda db, org, did: did != "d-1-first"
    )
    monkeypatch.setattr(ds, "_record_webhook_delivery", lambda db, org, did: True)

    first = _call(
        json.dumps({"event_type": "invoice.updated"}).encode(),
        delivery_id="d-1-first",
    )
    assert first.status == "accepted"
    replay = _call(
        json.dumps({"event_type": "invoice.updated"}).encode(),
        delivery_id="d-1-replay",
    )
    assert replay.status == "ok"
    assert "duplicate" in replay.message
    assert len(enqueued) == 1


def test_task_retries_locally_on_rate_limit():
    from app.tasks.dotmac_sub import process_dotmac_sub_webhook

    # The local-retry contract: paced, backing off, bounded.
    assert process_dotmac_sub_webhook.rate_limit == "30/m"
    assert process_dotmac_sub_webhook.retry_backoff is True
    assert process_dotmac_sub_webhook.max_retries == 8


def test_broker_outage_leaves_no_dedupe_record(monkeypatch):
    """Enqueue-first ordering: a 503 must not poison the sender's retry."""
    _configure(monkeypatch)
    import app.tasks.dotmac_sub as tasks

    recorded: list[str] = []
    monkeypatch.setattr(ds, "_webhook_delivery_seen", lambda db, org, did: False)
    monkeypatch.setattr(
        ds, "_record_webhook_delivery", lambda db, org, did: recorded.append(did)
    )

    def _boom(*a, **k):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr(tasks.process_dotmac_sub_webhook, "delay", _boom)

    with pytest.raises(HTTPException) as exc:
        _call(
            json.dumps({"event_type": "payment.received"}).encode(),
            delivery_id="d-broker-down",
        )
    assert exc.value.status_code == 503
    assert recorded == []  # nothing recorded -> sender retry re-attempts fully
