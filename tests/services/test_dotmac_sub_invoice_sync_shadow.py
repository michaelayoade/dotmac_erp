from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import ANY, Mock
from uuid import UUID

import pytest

from app.services.dotmac_sub.invoice_sync_outcomes import (
    InvoiceSyncDisposition,
    InvoiceSyncSourceKind,
    RecordInvoiceSyncOutcome,
)
from app.services.dotmac_sub.invoice_sync_shadow import (
    InvoiceSyncShadowContractError,
    observe_invoice_accounting_v2,
)

ORG_ID = UUID("10000000-0000-0000-0000-000000000001")
INVOICE_ID = UUID("20000000-0000-0000-0000-000000000001")
UPDATED_AT = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def _command(disposition: InvoiceSyncDisposition) -> RecordInvoiceSyncOutcome:
    return RecordInvoiceSyncOutcome(
        organization_id=ORG_ID,
        source_invoice_id=INVOICE_ID,
        source_updated_at=UPDATED_AT,
        source_kind=InvoiceSyncSourceKind.NATIVE,
        disposition=disposition,
        projection_fingerprint="a" * 64,
    )


def test_shadow_records_blocked_as_consumed_without_calling_posting(monkeypatch):
    db = Mock()
    db.execute.return_value.first.return_value = None
    client = Mock()
    client.get_invoice_accounting_sync_v2.return_value = [object()]
    monkeypatch.setattr(
        "app.services.dotmac_sub.invoice_sync_shadow._command",
        lambda organization_id, record: _command(InvoiceSyncDisposition.BLOCKED),
    )
    recorder = Mock(
        return_value=SimpleNamespace(
            replayed=False,
            resolved_prior_count=0,
        )
    )
    monkeypatch.setattr(
        "app.services.dotmac_sub.invoice_sync_shadow.record_invoice_sync_outcome",
        recorder,
    )

    result = observe_invoice_accounting_v2(db, client, ORG_ID)

    assert result.observed == 1
    assert result.blocked == 1
    assert result.ready == 0
    recorder.assert_called_once()
    client.get_invoice_accounting_sync_v2.assert_called_once_with(
        invoice_id=None,
        updated_since=None,
        on_parse_error=ANY,
    )


def test_shadow_rejects_parse_errors_so_caller_rolls_back(monkeypatch):
    db = Mock()
    db.execute.return_value.first.return_value = None
    client = Mock()

    def malformed_feed(**kwargs):
        kwargs["on_parse_error"](ValueError("bad row"))
        return iter(())

    client.get_invoice_accounting_sync_v2.side_effect = malformed_feed

    with pytest.raises(InvoiceSyncShadowContractError, match="rejected 1"):
        observe_invoice_accounting_v2(db, client, ORG_ID)


def test_shadow_is_bounded(monkeypatch):
    db = Mock()
    db.execute.return_value.first.return_value = None
    client = Mock()
    client.get_invoice_accounting_sync_v2.return_value = [object(), object()]
    monkeypatch.setattr(
        "app.services.dotmac_sub.invoice_sync_shadow._command",
        lambda organization_id, record: _command(InvoiceSyncDisposition.READY),
    )
    recorder = Mock(
        return_value=SimpleNamespace(replayed=False, resolved_prior_count=0)
    )
    monkeypatch.setattr(
        "app.services.dotmac_sub.invoice_sync_shadow.record_invoice_sync_outcome",
        recorder,
    )

    result = observe_invoice_accounting_v2(db, client, ORG_ID, batch_size=1)

    assert result.observed == 1
    assert result.truncated is True
    recorder.assert_called_once()
