from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.finance.ar.dotmac_sub_invoice_sync_outcome import (
    DotmacSubInvoiceSyncOutcome,
)
from app.services.dotmac_sub.invoice_sync_outcomes import (
    InvoiceSyncDisposition,
    InvoiceSyncIssueCode,
    InvoiceSyncIssueEvidence,
    InvoiceSyncOutcomeError,
    InvoiceSyncSourceKind,
    RecordInvoiceSyncOutcome,
    record_invoice_sync_outcome,
)

ORGANIZATION_ID = uuid4()
INVOICE_ID = uuid4()


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _command(
    *, disposition: InvoiceSyncDisposition, revision: datetime, value: str
) -> RecordInvoiceSyncOutcome:
    issues = (
        (
            InvoiceSyncIssueEvidence(
                code=InvoiceSyncIssueCode.HEADER_TAX_MISMATCH,
                expected_amount=Decimal("75.00"),
                actual_amount=Decimal("0.00"),
            ),
        )
        if disposition is InvoiceSyncDisposition.BLOCKED
        else ()
    )
    return RecordInvoiceSyncOutcome(
        organization_id=ORGANIZATION_ID,
        source_invoice_id=INVOICE_ID,
        source_updated_at=revision,
        source_kind=InvoiceSyncSourceKind.NATIVE,
        disposition=disposition,
        projection_fingerprint=_fingerprint(value),
        issues=issues,
        observed_at=revision + timedelta(seconds=1),
    )


def test_records_and_replays_identical_blocked_revision(db_session) -> None:
    revision = datetime(2026, 9, 6, 12, tzinfo=UTC)
    command = _command(
        disposition=InvoiceSyncDisposition.BLOCKED,
        revision=revision,
        value="blocked-v1",
    )

    first = record_invoice_sync_outcome(db_session, command)
    second = record_invoice_sync_outcome(db_session, command)

    assert first.replayed is False
    assert second.replayed is True
    assert second.outcome_id == first.outcome_id
    assert second.occurrence_count == 2
    stored = db_session.scalar(
        select(DotmacSubInvoiceSyncOutcome).where(
            DotmacSubInvoiceSyncOutcome.outcome_id == first.outcome_id
        )
    )
    assert stored is not None
    assert stored.issue_count == 1
    assert len(stored.issues) == 1


def test_refuses_changed_projection_at_same_revision(db_session) -> None:
    revision = datetime(2026, 9, 6, 13, tzinfo=UTC)
    record_invoice_sync_outcome(
        db_session,
        _command(
            disposition=InvoiceSyncDisposition.BLOCKED,
            revision=revision,
            value="first",
        ),
    )

    with pytest.raises(
        InvoiceSyncOutcomeError, match="same Self-Care invoice revision"
    ):
        record_invoice_sync_outcome(
            db_session,
            _command(
                disposition=InvoiceSyncDisposition.BLOCKED,
                revision=revision,
                value="different",
            ),
        )


def test_later_ready_revision_resolves_prior_blocked_evidence(db_session) -> None:
    blocked_at = datetime(2026, 9, 6, 14, tzinfo=UTC)
    ready_at = blocked_at + timedelta(hours=1)
    blocked = record_invoice_sync_outcome(
        db_session,
        _command(
            disposition=InvoiceSyncDisposition.BLOCKED,
            revision=blocked_at,
            value="blocked",
        ),
    )

    ready = record_invoice_sync_outcome(
        db_session,
        _command(
            disposition=InvoiceSyncDisposition.READY,
            revision=ready_at,
            value="ready",
        ),
    )

    assert ready.resolved_prior_count == 1
    prior = db_session.get(DotmacSubInvoiceSyncOutcome, blocked.outcome_id)
    assert prior is not None
    assert prior.resolved_at == ready_at + timedelta(seconds=1)
