"""Remote evidence must not hold a write transaction or bypass safe cursors."""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.services.dotmac_sub.sync import _invoices as invoices
from app.services.dotmac_sub.sync._base import (
    InvoiceSourceAccountingMismatchError,
    SyncWatermarkPosition,
)


def setup(monkeypatch, *, late_parse_failure=False):
    state = {"transaction": True, "events": []}
    service = invoices.InvoiceSyncMixin()
    service.organization_id = uuid4()
    service.db = MagicMock()
    row = SimpleNamespace(
        id=str(uuid4()),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        invoice_number="SYNTHETIC-1",
        currency="NGN",
    )
    service.client = MagicMock()

    def rows(**kwargs):
        yield row
        if late_parse_failure:
            kwargs["on_parse_error"](SimpleNamespace(updated_at=None))

    service.client.get_invoices.side_effect = rows
    service._get_sync_watermark_position = lambda _: SyncWatermarkPosition(None, None)
    service._advance_sync_watermark_position = MagicMock()
    failure = InvoiceSourceAccountingMismatchError(
        "Synthetic source mismatch",
        dedupe_key=("synthetic",),
        line_subtotal=Decimal("100"),
        line_tax=Decimal("0"),
        header_subtotal=Decimal("100"),
        header_tax=Decimal("7.5"),
        header_total=Decimal("107.5"),
    )
    service._sync_single_invoice = MagicMock(side_effect=failure)

    def commit():
        state["transaction"] = False
        state["events"].append("commit")

    service.db.commit.side_effect = commit

    def begin():
        state["transaction"] = True
        state["events"].append("savepoint")
        return MagicMock()

    service.db.begin_nested.side_effect = begin

    def prime():
        state["transaction"] = True
        state["events"].append("prime")

    service._reprime_tenant_context = prime

    def fetch(*args, **kwargs):
        assert state["transaction"] is False
        service._advance_sync_watermark_position.assert_not_called()
        state["events"].append("fetch")
        return SimpleNamespace()

    fetch_mock = MagicMock(side_effect=fetch)

    def record(*args):
        assert state["transaction"] is True
        state["events"].append("record")
        return SimpleNamespace(outcome_id=uuid4())

    monkeypatch.setattr(
        invoices, "fetch_blocked_invoice_accounting_revision", fetch_mock
    )
    monkeypatch.setattr(invoices, "record_invoice_sync_outcome", record)
    return service, row, state, fetch_mock


def test_remote_lookup_occurs_between_commit_and_new_savepoint(monkeypatch):
    service, row, state, _ = setup(monkeypatch)
    result = service.sync_invoices()
    assert result.skipped == 1
    assert state["events"] == [
        "savepoint",
        "commit",
        "fetch",
        "prime",
        "savepoint",
        "record",
    ]
    cursor = service._advance_sync_watermark_position.call_args.args[1]
    assert cursor.external_id == row.id


def test_later_unpositioned_failure_still_freezes_original_cursor(monkeypatch):
    service, _, state, _ = setup(monkeypatch, late_parse_failure=True)
    result = service.sync_invoices()
    assert result.skipped == 1
    assert "record" in state["events"]
    service._advance_sync_watermark_position.assert_not_called()


def test_exhausted_remote_budget_stops_before_another_lookup(monkeypatch):
    service, _, _, fetch = setup(monkeypatch)
    monkeypatch.setattr(invoices, "_QUARANTINE_REMOTE_BUDGET_SECONDS", 0)
    result = service.sync_invoices()
    fetch.assert_not_called()
    assert "time budget" in result.message
    cursor = service._advance_sync_watermark_position.call_args.args[1]
    assert cursor.watermark_at is None
    assert cursor.external_id is None


def test_upstream_throttle_defers_batch_without_skipping_source_revision(monkeypatch):
    service, _, _, fetch = setup(monkeypatch)
    fetch.side_effect = invoices.DotmacSubRateLimitError("synthetic throttle")
    result = service.sync_invoices()
    assert fetch.call_count == 1
    assert result.skipped == 0
    assert "rate limit" in result.message
    cursor = service._advance_sync_watermark_position.call_args.args[1]
    assert cursor.external_id is None


def test_outcome_conflict_keeps_revision_unconsumed(monkeypatch):
    from app.services.dotmac_sub.invoice_sync_outcomes import InvoiceSyncOutcomeError

    service, _, _, _ = setup(monkeypatch)
    monkeypatch.setattr(
        invoices,
        "record_invoice_sync_outcome",
        MagicMock(side_effect=InvoiceSyncOutcomeError("synthetic conflict")),
    )
    result = service.sync_invoices()
    assert result.skipped == 0
    assert result.errors
    cursor = service._advance_sync_watermark_position.call_args.args[1]
    assert cursor.external_id is None


def test_database_checkpoint_error_aborts_instead_of_being_quarantined(monkeypatch):
    from sqlalchemy.exc import OperationalError

    service, _, _, fetch = setup(monkeypatch)
    service.db.commit.side_effect = OperationalError(
        "synthetic", {}, RuntimeError("db")
    )
    with pytest.raises(OperationalError):
        service.sync_invoices()
    fetch.assert_not_called()
