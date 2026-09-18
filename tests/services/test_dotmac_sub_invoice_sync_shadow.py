from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import ANY, Mock
from uuid import UUID

import pytest
from sqlalchemy import select

from app.models.finance.ar.dotmac_sub_invoice_sync_outcome import (
    DotmacSubInvoiceSyncOutcome,
)
from app.models.finance.ar.dotmac_sub_invoice_sync_outcome_legacy import (
    DotmacSubInvoiceSyncIssueLegacy,
    DotmacSubInvoiceSyncOutcomeLegacy,
)
from app.services.dotmac_sub.client import (
    DotmacSubClient,
    DotmacSubConfig,
    InvoiceAccountingSyncRecord,
)
from app.services.dotmac_sub.invoice_sync_outcomes import (
    SUPPORTED_DIGEST_VERSION,
    InvoiceSyncDisposition,
    InvoiceSyncSourceKind,
    RecordInvoiceSyncOutcome,
)
from app.services.dotmac_sub.invoice_sync_shadow import (
    InvoiceSyncShadowContractError,
    _command as _shadow_command,
    _latest_canonical_position,
    observe_invoice_accounting_v2,
    record_blocked_invoice_accounting_revision,
)

ORG_ID = UUID("10000000-0000-0000-0000-000000000001")
INVOICE_ID = UUID("20000000-0000-0000-0000-000000000001")
UPDATED_AT = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _legacy_tables(db_session):
    """Create the frozen legacy archive tables on this file's SQLite engine.

    ``tests/conftest.py``'s ``SQLITE_COMPATIBLE_TABLES`` allowlist only lists
    the canonical ``DotmacSubInvoiceSyncOutcome``/``DotmacSubInvoiceSyncIssue``
    tables — touching that shared fixture is out of scope for this change.
    The two tests here that construct a ``DotmacSubInvoiceSyncOutcomeLegacy``
    row need the table to exist first; harmless no-op for every other test in
    this file, the same shape as
    ``tests/api/test_integrator_observations.py``'s
    ``_kernel_idempotency_tables`` fixture.
    """
    engine = db_session.get_bind()
    # SQLite can't parse Postgres server-defaults like gen_random_uuid(); drop
    # them (Python-side defaults still supply the PK), mirroring the shared
    # harness's ``_strip_sqlite_server_defaults`` and the identical local
    # idiom in ``tests/services/test_dotmac_sub_payment_idempotency.py``.
    for table in (
        DotmacSubInvoiceSyncOutcomeLegacy.__table__,
        DotmacSubInvoiceSyncIssueLegacy.__table__,
    ):
        for col in table.columns:
            default = col.server_default
            if default is not None and "gen_random_uuid" in str(
                getattr(default, "arg", default)
            ):
                col.server_default = None
    DotmacSubInvoiceSyncOutcomeLegacy.__table__.create(engine, checkfirst=True)
    DotmacSubInvoiceSyncIssueLegacy.__table__.create(engine, checkfirst=True)
    yield


def _command(disposition: InvoiceSyncDisposition) -> RecordInvoiceSyncOutcome:
    return RecordInvoiceSyncOutcome(
        organization_id=ORG_ID,
        source_invoice_id=INVOICE_ID,
        source_updated_at=UPDATED_AT,
        source_kind=InvoiceSyncSourceKind.NATIVE,
        disposition=disposition,
        projection_fingerprint="a" * 64,
        digest_version=SUPPORTED_DIGEST_VERSION,
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


def test_latest_canonical_position_ignores_legacy_evidence(db_session) -> None:
    """The canonical cursor must return ``None`` when the canonical table is
    empty, even though a legacy row exists at a real position for this
    organization — legacy evidence lives in a physically separate table this
    function never queries."""
    legacy = DotmacSubInvoiceSyncOutcomeLegacy(
        organization_id=ORG_ID,
        source_invoice_id=INVOICE_ID,
        source_updated_at=UPDATED_AT,
        contract_version="invoice-accounting-sync.v2",
        source_kind=InvoiceSyncSourceKind.NATIVE.value,
        disposition=InvoiceSyncDisposition.READY.value,
        projection_fingerprint="b" * 64,
        issue_count=0,
        occurrence_count=1,
    )
    db_session.add(legacy)
    db_session.flush()

    assert _latest_canonical_position(db_session, ORG_ID) is None


def test_observe_reobserves_an_invoice_whose_only_evidence_is_legacy(
    db_session,
) -> None:
    """Given a canonical table that is empty because the only existing
    evidence for this invoice is a legacy row,
    ``observe_invoice_accounting_v2`` must actually re-observe it (not skip
    it) — the natural consequence of the cursor returning ``None``."""
    legacy = DotmacSubInvoiceSyncOutcomeLegacy(
        organization_id=ORG_ID,
        source_invoice_id=INVOICE_ID,
        source_updated_at=UPDATED_AT,
        contract_version="invoice-accounting-sync.v2",
        source_kind=InvoiceSyncSourceKind.NATIVE.value,
        disposition=InvoiceSyncDisposition.READY.value,
        projection_fingerprint="b" * 64,
        issue_count=0,
        occurrence_count=1,
    )
    db_session.add(legacy)
    db_session.flush()

    record = _record(disposition="ready", digest_version=SUPPORTED_DIGEST_VERSION)
    client = Mock()
    client.get_invoice_accounting_sync_v2.return_value = [record]

    result = observe_invoice_accounting_v2(db_session, client, ORG_ID)

    assert result.observed == 1
    client.get_invoice_accounting_sync_v2.assert_called_once_with(
        invoice_id=None,
        updated_since=None,
        on_parse_error=ANY,
    )
    stored = db_session.scalar(
        select(DotmacSubInvoiceSyncOutcome).where(
            DotmacSubInvoiceSyncOutcome.organization_id == ORG_ID,
            DotmacSubInvoiceSyncOutcome.source_invoice_id == INVOICE_ID,
        )
    )
    assert stored is not None
    assert stored.digest_version == SUPPORTED_DIGEST_VERSION
