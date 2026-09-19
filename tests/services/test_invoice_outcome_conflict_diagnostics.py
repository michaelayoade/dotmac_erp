"""Regression tests for safe, actionable invoice revision conflicts."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.services.dotmac_sub.invoice_sync_outcomes import (
    CONTRACT_VERSION,
    SUPPORTED_DIGEST_VERSION,
    InvoiceSyncDisposition,
    InvoiceSyncIssueCode,
    InvoiceSyncIssueEvidence,
    InvoiceSyncOutcomeConflictError,
    InvoiceSyncOutcomeError,
    InvoiceSyncSourceKind,
    RecordInvoiceSyncOutcome,
    record_invoice_sync_outcome,
)


@pytest.fixture
def replay():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    command = RecordInvoiceSyncOutcome(
        organization_id=uuid4(),
        source_invoice_id=uuid4(),
        source_updated_at=now,
        source_kind=InvoiceSyncSourceKind.NATIVE,
        disposition=InvoiceSyncDisposition.BLOCKED,
        projection_fingerprint="a" * 64,
        digest_version=SUPPORTED_DIGEST_VERSION,
        issues=(InvoiceSyncIssueEvidence(InvoiceSyncIssueCode.HEADER_TAX_MISMATCH),),
        observed_at=now,
    )
    existing = SimpleNamespace(
        outcome_id=uuid4(),
        contract_version=CONTRACT_VERSION,
        source_kind="native",
        disposition="blocked",
        projection_fingerprint="a" * 64,
        digest_version=SUPPORTED_DIGEST_VERSION,
        issue_count=1,
        occurrence_count=3,
        last_seen_at=now,
    )
    db = MagicMock()
    db.scalar.return_value = existing
    return db, command, existing


@pytest.mark.parametrize(
    ("field", "previous"),
    [
        ("contract_version", "invoice-accounting-sync.v1"),
        ("source_kind", "splynx_legacy"),
        ("disposition", "ready"),
        ("projection_fingerprint", "b" * 64),
        ("issue_count", 2),
    ],
)
def test_same_digest_version_conflict_identifies_field_and_never_mutates_evidence(
    replay, caplog, field, previous
):
    db, command, existing = replay
    incoming = getattr(existing, field)
    setattr(existing, field, previous)
    before = vars(existing).copy()

    with pytest.raises(InvoiceSyncOutcomeConflictError) as caught:
        record_invoice_sync_outcome(db, command)

    assert isinstance(caught.value, InvoiceSyncOutcomeError)
    assert field in str(caught.value)
    assert caught.value.differences == {
        field: {"existing": previous, "incoming": incoming}
    }
    assert vars(existing) == before
    db.add.assert_not_called()
    db.flush.assert_not_called()
    db.execute.assert_not_called()
    db.commit.assert_not_called()
    record = next(
        row
        for row in caplog.records
        if getattr(row, "event", None) == "dotmac_sub_invoice_outcome_conflict"
    )
    assert record.differences == caught.value.differences
    assert record.source_invoice_id == str(command.source_invoice_id)
    assert not hasattr(record, "invoice_payload")


def test_identical_revision_replays_without_conflict(replay, caplog):
    db, command, existing = replay

    receipt = record_invoice_sync_outcome(db, command)

    assert receipt.replayed is True
    assert receipt.occurrence_count == 4
    assert receipt.outcome_id == existing.outcome_id
    assert existing.projection_fingerprint == command.projection_fingerprint
    db.flush.assert_called_once_with()
    db.commit.assert_not_called()
    assert not any(
        getattr(record, "event", None) == "dotmac_sub_invoice_outcome_conflict"
        for record in caplog.records
    )


def test_all_differences_are_reported_together(replay):
    db, command, existing = replay
    existing.disposition = "ready"
    existing.projection_fingerprint = "c" * 64

    with pytest.raises(InvoiceSyncOutcomeConflictError) as caught:
        record_invoice_sync_outcome(db, command)

    assert set(caught.value.differences) == {
        "disposition",
        "projection_fingerprint",
    }
    assert existing.occurrence_count == 3
