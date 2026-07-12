"""Tests for the dotmac_sub AR incremental-sync watermark.

Incident: the AR pull re-listed every invoice each cycle via OFFSET pagination
over an unindexed ``created_at`` sort, starving dotmac_sub's DB pool. The fix
pulls only rows with ``updated_at >= watermark`` and advances a per-entity
high-watermark. These tests cover:

- the pure advance logic (first-run full pull, advance to max, freeze at the
  first failed row, advance-only),
- the client forwarding ``updated_since`` + ascending order to the API,
- records parsing ``updated_at``,
- the DB-backed watermark get/advance helpers.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from app.models.finance.ar.external_sync import EntityType
from app.services.dotmac_sub.client import (
    DotmacSubClient,
    DotmacSubConfig,
    InvoiceLineRecord,
    InvoiceRecord,
    _watermark_params,
)
from app.services.dotmac_sub.sync._base import BaseSyncMixin, next_watermark
from app.services.dotmac_sub.sync._invoices import _invoice_hash_payload

_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _norm(dt):
    """Normalize to UTC-naive so tz-aware (Postgres) and tz-naive (SQLite test
    backend) reads of a ``DateTime(timezone=True)`` column compare equal."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


# ---------------------------------------------------------------------------
# Pure watermark-advance logic
# ---------------------------------------------------------------------------


def test_next_watermark_first_run_advances_to_max() -> None:
    # No prior watermark, no errors → advance to the highest processed row.
    assert next_watermark(None, _T0 + timedelta(days=2), None) == _T0 + timedelta(
        days=2
    )


def test_next_watermark_empty_pull_keeps_current() -> None:
    # Nothing seen (delta empty) → leave the cursor where it was.
    assert next_watermark(_T0, None, None) == _T0
    assert next_watermark(None, None, None) is None


def test_next_watermark_freezes_at_first_error() -> None:
    # A failed row must not be skipped: park the cursor at the earliest failure
    # (inclusive >= re-pulls it next cycle), even if later rows succeeded.
    max_ok = _T0 + timedelta(days=5)
    min_error = _T0 + timedelta(days=2)
    assert next_watermark(_T0, max_ok, min_error) == min_error


def test_next_watermark_is_advance_only() -> None:
    # Never move backward below the current cursor.
    assert next_watermark(_T0 + timedelta(days=10), _T0, None) == _T0 + timedelta(
        days=10
    )


# ---------------------------------------------------------------------------
# Client param building + forwarding
# ---------------------------------------------------------------------------


def test_watermark_params_requests_ascending_order_when_watermarked() -> None:
    params = _watermark_params(None, None, "2026-01-01T12:00:00+00:00")
    assert params == {
        "updated_since": "2026-01-01T12:00:00+00:00",
        "order_by": "updated_at",
        "order_dir": "asc",
    }


def test_watermark_params_omits_order_without_watermark() -> None:
    # Full pull (no watermark) keeps the API default ordering.
    assert _watermark_params("acct-1", "paid", None) == {
        "account_id": "acct-1",
        "status": "paid",
    }


def _client_capturing_params() -> tuple[DotmacSubClient, list[dict]]:
    client = DotmacSubClient(DotmacSubConfig(api_url="https://x", api_token="t"))
    captured: list[dict] = []

    def _fake_request(method, endpoint, params=None, **kwargs):
        captured.append(dict(params or {}))
        return {"items": []}  # empty page → generator stops immediately

    client._request = MagicMock(side_effect=_fake_request)  # type: ignore[method-assign]
    return client, captured


def test_get_invoices_forwards_updated_since() -> None:
    client, captured = _client_capturing_params()
    list(client.get_invoices(updated_since="2026-05-01T00:00:00+00:00"))
    assert captured[0]["updated_since"] == "2026-05-01T00:00:00+00:00"
    assert captured[0]["order_by"] == "updated_at"
    assert captured[0]["order_dir"] == "asc"
    assert captured[0]["limit"] == 500


def test_get_invoices_uses_lightweight_sync_feed() -> None:
    client, _ = _client_capturing_params()
    list(client.get_invoices())
    client._request.assert_called_once()
    assert client._request.call_args.args[:2] == ("GET", "/invoices/sync")


def test_get_invoices_without_watermark_sends_no_updated_since() -> None:
    client, captured = _client_capturing_params()
    list(client.get_invoices())
    assert "updated_since" not in captured[0]


def test_get_payments_and_credit_notes_forward_updated_since() -> None:
    client, captured = _client_capturing_params()
    list(client.get_payments(updated_since="2026-05-01T00:00:00+00:00"))
    list(client.get_credit_notes(updated_since="2026-05-01T00:00:00+00:00"))
    assert all(c.get("updated_since") == "2026-05-01T00:00:00+00:00" for c in captured)


def test_parse_invoice_reads_updated_at() -> None:
    client = DotmacSubClient(DotmacSubConfig(api_url="x", api_token="t"))
    rec = client._parse_invoice(
        {"id": "1", "account_id": "a", "updated_at": "2026-06-01T10:00:00+00:00"}
    )
    assert rec.updated_at == "2026-06-01T10:00:00+00:00"


def test_invoice_hash_payload_tracks_line_and_header_changes() -> None:
    line = InvoiceLineRecord(
        id="line-1",
        description="Internet service",
        quantity=1,
        unit_price=100,
        amount=100,
    )
    invoice = InvoiceRecord(
        id="inv-1",
        account_id="acct-1",
        invoice_number="INV-1",
        status="issued",
        currency="NGN",
        subtotal=100,
        tax_total=0,
        total=100,
        balance_due=100,
        due_at="2026-06-30",
        memo="Original",
        lines=[line],
    )
    original = _invoice_hash_payload(invoice)

    line.description = "Corrected service"
    invoice.memo = "Corrected"
    changed = _invoice_hash_payload(invoice)

    assert changed != original
    assert changed["lines"][0]["description"] == "Corrected service"
    assert changed["memo"] == "Corrected"


# ---------------------------------------------------------------------------
# DB-backed watermark helpers
# ---------------------------------------------------------------------------


class _WatermarkHarness(BaseSyncMixin):
    """Minimal BaseSyncMixin that skips the config/DB lookup in __init__."""

    def __init__(self, db, organization_id):
        self.db = db
        self.organization_id = organization_id


def test_watermark_get_advance_roundtrip(db_session) -> None:
    org = uuid.uuid4()
    h = _WatermarkHarness(db_session, org)

    # Never synced → None (caller does a full pull).
    assert h._get_sync_watermark(EntityType.INVOICE) is None

    # First advance inserts the cursor.
    h._advance_sync_watermark(EntityType.INVOICE, _T0 + timedelta(days=1))
    db_session.flush()
    assert _norm(h._get_sync_watermark(EntityType.INVOICE)) == _norm(
        _T0 + timedelta(days=1)
    )

    # Advancing forward moves the cursor.
    h._advance_sync_watermark(EntityType.INVOICE, _T0 + timedelta(days=3))
    db_session.flush()
    assert _norm(h._get_sync_watermark(EntityType.INVOICE)) == _norm(
        _T0 + timedelta(days=3)
    )

    # A backward value is ignored (advance-only).
    h._advance_sync_watermark(EntityType.INVOICE, _T0)
    db_session.flush()
    assert _norm(h._get_sync_watermark(EntityType.INVOICE)) == _norm(
        _T0 + timedelta(days=3)
    )


def test_watermark_is_per_entity_type(db_session) -> None:
    org = uuid.uuid4()
    h = _WatermarkHarness(db_session, org)
    h._advance_sync_watermark(EntityType.INVOICE, _T0 + timedelta(days=1))
    h._advance_sync_watermark(EntityType.PAYMENT, _T0 + timedelta(days=2))
    db_session.flush()
    assert _norm(h._get_sync_watermark(EntityType.INVOICE)) == _norm(
        _T0 + timedelta(days=1)
    )
    assert _norm(h._get_sync_watermark(EntityType.PAYMENT)) == _norm(
        _T0 + timedelta(days=2)
    )
    assert h._get_sync_watermark(EntityType.CREDIT_NOTE) is None
