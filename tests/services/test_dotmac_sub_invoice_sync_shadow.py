from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import ANY, Mock
from uuid import UUID

import pytest

from app.services.dotmac_sub.client import (
    DotmacSubClient,
    DotmacSubConfig,
    InvoiceAccountingSyncRecord,
)
from app.services.dotmac_sub.invoice_sync_outcomes import (
    InvoiceSyncDisposition,
    InvoiceSyncSourceKind,
    RecordInvoiceSyncOutcome,
)
from app.services.dotmac_sub.invoice_sync_shadow import (
    InvoiceSyncShadowContractError,
    _command as _shadow_command,
    observe_invoice_accounting_v2,
    record_blocked_invoice_accounting_revision,
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


def _accounting_v2_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "contract_version": "invoice-accounting-sync.v2",
        "source_kind": "native",
        "source_invoice_id": str(INVOICE_ID),
        "source_splynx_invoice_id": None,
        "account_id": "22222222-2222-2222-2222-222222222222",
        "account": {
            "id": "22222222-2222-2222-2222-222222222222",
            "display_name": "Shadow account",
            "updated_at": "2026-09-06T10:00:00Z",
        },
        "invoice_number": "INV-1",
        "status": "issued",
        "currency": "NGN",
        "subtotal_before_discount": "1000.00",
        "discount_type": None,
        "discount_value": None,
        "discount_amount": "0.00",
        "discounted_subtotal": "1000.00",
        "tax_total": "75.00",
        "total": "1075.00",
        "balance_due": "1075.00",
        "issued_at": "2026-09-05T10:00:00Z",
        "due_at": None,
        "paid_at": None,
        "memo": None,
        "is_proforma": False,
        "updated_at": UPDATED_AT.isoformat(),
        "disposition": "ready",
        "digest_version": 1,
        "projection_digest": "c" * 64,
        "issues": [],
        "lines": [],
    }
    payload.update(overrides)
    return payload


def _record(**overrides: object) -> InvoiceAccountingSyncRecord:
    client = DotmacSubClient(DotmacSubConfig(api_url="https://sub.test", api_token="t"))
    return client._parse_invoice_accounting_sync_v2(_accounting_v2_payload(**overrides))


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


def test_targeted_blocked_revision_uses_durable_outcome_owner(monkeypatch):
    db = Mock()
    client = Mock()
    record = object()
    client.get_invoice_accounting_sync_v2.return_value = [record]
    command = _command(InvoiceSyncDisposition.BLOCKED)
    monkeypatch.setattr(
        "app.services.dotmac_sub.invoice_sync_shadow._command",
        lambda organization_id, observed: command,
    )
    receipt = SimpleNamespace(outcome_id=UUID(int=9))
    recorder = Mock(return_value=receipt)
    monkeypatch.setattr(
        "app.services.dotmac_sub.invoice_sync_shadow.record_invoice_sync_outcome",
        recorder,
    )

    result = record_blocked_invoice_accounting_revision(
        db,
        client,
        ORG_ID,
        invoice_id=INVOICE_ID,
        expected_updated_at=UPDATED_AT,
    )

    assert result is receipt
    recorder.assert_called_once_with(db, command)
    client.get_invoice_accounting_sync_v2.assert_called_once_with(
        invoice_id=str(INVOICE_ID),
        on_parse_error=ANY,
    )


def test_command_forwards_projection_digest_verbatim() -> None:
    record = _record(projection_digest="d" * 64)

    command = _shadow_command(ORG_ID, record)

    assert command.projection_fingerprint == record.projection_digest
    assert command.projection_fingerprint == "d" * 64


def test_command_rejects_unsupported_digest_version() -> None:
    record = _record(digest_version=2)

    with pytest.raises(InvoiceSyncShadowContractError, match="digest_version"):
        _shadow_command(ORG_ID, record)


def test_shadow_rejects_unsupported_digest_version_before_recording(monkeypatch):
    """An unsupported digest_version must never reach the outcome recorder."""
    db = Mock()
    db.execute.return_value.first.return_value = None
    client = Mock()
    client.get_invoice_accounting_sync_v2.return_value = [_record(digest_version=2)]
    recorder = Mock()
    monkeypatch.setattr(
        "app.services.dotmac_sub.invoice_sync_shadow.record_invoice_sync_outcome",
        recorder,
    )

    with pytest.raises(InvoiceSyncShadowContractError, match="digest_version"):
        observe_invoice_accounting_v2(db, client, ORG_ID)

    recorder.assert_not_called()


def test_targeted_revision_refuses_non_blocked_source(monkeypatch):
    db = Mock()
    client = Mock()
    client.get_invoice_accounting_sync_v2.return_value = [object()]
    monkeypatch.setattr(
        "app.services.dotmac_sub.invoice_sync_shadow._command",
        lambda organization_id, observed: _command(InvoiceSyncDisposition.READY),
    )

    with pytest.raises(InvoiceSyncShadowContractError, match="not blocked"):
        record_blocked_invoice_accounting_revision(
            db,
            client,
            ORG_ID,
            invoice_id=INVOICE_ID,
            expected_updated_at=UPDATED_AT,
        )
