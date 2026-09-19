from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.finance.ar.dotmac_sub_invoice_sync_outcome import (
    DotmacSubInvoiceSyncOutcome,
)
from app.models.finance.ar.dotmac_sub_invoice_sync_outcome_legacy import (
    DotmacSubInvoiceSyncIssueLegacy,
    DotmacSubInvoiceSyncOutcomeLegacy,
)
from app.services.dotmac_sub.invoice_sync_outcomes import (
    SUPPORTED_DIGEST_VERSION,
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


@pytest.fixture(autouse=True)
def _legacy_tables(db_session):
    """Create the frozen legacy archive tables on this file's SQLite engine.

    ``tests/conftest.py`` provisions its SQLite engine from a hand-maintained
    ``SQLITE_COMPATIBLE_TABLES`` allowlist (not ``Base.metadata.create_all``),
    and that allowlist only lists the canonical
    ``DotmacSubInvoiceSyncOutcome``/``DotmacSubInvoiceSyncIssue`` tables — it
    is out of scope for this change to touch (shared fixture). This module is
    the first ERP caller that needs the ``_legacy`` tables to exist for a
    unit test, so it provisions them itself, the same way
    ``tests/api/test_integrator_observations.py``'s
    route-level tests provision the HTTP idempotency ledger
    tables the shared fixture also does not know about.
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


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _command(
    *,
    disposition: InvoiceSyncDisposition,
    revision: datetime,
    value: str,
    digest_version: int = SUPPORTED_DIGEST_VERSION,
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
        digest_version=digest_version,
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
    # SQLite drops timezone metadata even for DateTime(timezone=True); compare
    # the UTC instant rather than the backend-specific returned wrapper.
    assert prior.resolved_at.replace(tzinfo=UTC) == ready_at + timedelta(seconds=1)


def test_legacy_row_at_same_key_does_not_block_a_canonical_write(db_session) -> None:
    """A legacy row at the same key, with a different fingerprint, must not be
    seen by the canonical stability check at all — it lives in a physically
    separate table (``DotmacSubInvoiceSyncOutcomeLegacy``), not merely a
    different scheme value in the same row set."""
    revision = datetime(2026, 9, 18, 9, tzinfo=UTC)
    legacy = DotmacSubInvoiceSyncOutcomeLegacy(
        organization_id=ORGANIZATION_ID,
        source_invoice_id=INVOICE_ID,
        source_updated_at=revision,
        contract_version="invoice-accounting-sync.v2",
        source_kind=InvoiceSyncSourceKind.NATIVE.value,
        disposition=InvoiceSyncDisposition.READY.value,
        projection_fingerprint=_fingerprint("legacy-fingerprint-a"),
        issue_count=0,
        occurrence_count=1,
    )
    db_session.add(legacy)
    db_session.flush()

    canonical_command = _command(
        disposition=InvoiceSyncDisposition.READY,
        revision=revision,
        value="canonical-fingerprint-b",
    )

    receipt = record_invoice_sync_outcome(db_session, canonical_command)

    assert receipt.replayed is False
    stored = db_session.scalar(
        select(DotmacSubInvoiceSyncOutcome).where(
            DotmacSubInvoiceSyncOutcome.outcome_id == receipt.outcome_id
        )
    )
    assert stored is not None
    assert stored.projection_fingerprint == _fingerprint("canonical-fingerprint-b")
    assert stored.digest_version == SUPPORTED_DIGEST_VERSION


def test_two_canonical_observations_same_key_different_fingerprint_still_raises(
    db_session,
) -> None:
    """The same-content invariant keeps biting within the canonical scheme —
    not just before this change, and not only over an empty/never-triggered
    set. Both commands here carry the same explicit ``digest_version``."""
    revision = datetime(2026, 9, 18, 10, tzinfo=UTC)
    record_invoice_sync_outcome(
        db_session,
        _command(
            disposition=InvoiceSyncDisposition.READY,
            revision=revision,
            value="canonical-first",
        ),
    )

    with pytest.raises(
        InvoiceSyncOutcomeError, match="same Self-Care invoice revision"
    ):
        record_invoice_sync_outcome(
            db_session,
            _command(
                disposition=InvoiceSyncDisposition.READY,
                revision=revision,
                value="canonical-second",
            ),
        )


def test_rejects_unsupported_digest_version(db_session) -> None:
    command = _command(
        disposition=InvoiceSyncDisposition.READY,
        revision=datetime(2026, 9, 18, 11, tzinfo=UTC),
        value="wrong-version",
        digest_version=SUPPORTED_DIGEST_VERSION + 1,
    )

    with pytest.raises(InvoiceSyncOutcomeError, match="digest_version"):
        record_invoice_sync_outcome(db_session, command)


def test_uppercase_fingerprint_is_rejected_at_the_db_check_constraint_level(
    db_session,
) -> None:
    """Belt-and-braces (matches this repo's existing DB-level constraint
    testing style, e.g. RLS/privilege canaries elsewhere in ``tests/``):
    the pydantic-layer validator in ``app.schemas.integrator_observation``
    already rejects an uppercase-containing ``projection_fingerprint``, but
    ``ck_sub_invoice_outcome_fingerprint`` must independently enforce it at
    the database level, for any writer that bypasses the API schema."""
    outcome = DotmacSubInvoiceSyncOutcome(
        organization_id=ORGANIZATION_ID,
        source_invoice_id=INVOICE_ID,
        source_updated_at=datetime(2026, 9, 18, 12, tzinfo=UTC),
        contract_version="invoice-accounting-sync.v2",
        source_kind=InvoiceSyncSourceKind.NATIVE.value,
        disposition=InvoiceSyncDisposition.READY.value,
        projection_fingerprint=_fingerprint("uppercase-check").upper(),
        digest_version=SUPPORTED_DIGEST_VERSION,
        issue_count=0,
        occurrence_count=1,
    )
    db_session.add(outcome)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_nan_amount_issues_with_a_tied_fingerprint_raise_domain_error_not_typeerror(
    db_session,
) -> None:
    """Sensitivity proof for the sort-key hardening in ``_validated``.

    Two issue entries with identical code/line/amounts (both ``Decimal("NaN")``)
    produce the SAME string-rendered fingerprint (``format(Decimal("NaN"), "f")``
    is the stable string ``"NaN"`` every time), while the raw Decimals compare
    ``NaN != NaN``. Before the fix, Python's tuple-comparison fallback in
    ``sorted(...)`` would reach past the tied fingerprint strings and compare
    the two ``InvoiceSyncIssueEvidence`` instances directly — a dataclass
    with no ``__lt__`` — raising a bare ``TypeError``. This is only reachable
    from a caller that bypasses ``app.schemas.integrator_observation``'s
    pydantic validation (verified separately: pydantic 2.11's bare
    ``Decimal`` field type rejects ``NaN`` by default, so the wire route
    itself cannot trigger this — see the task report), i.e. any other
    direct internal caller of this frozen module.
    """
    same_line_id = uuid4()
    command = RecordInvoiceSyncOutcome(
        organization_id=ORGANIZATION_ID,
        source_invoice_id=INVOICE_ID,
        source_updated_at=datetime(2026, 9, 18, 13, tzinfo=UTC),
        source_kind=InvoiceSyncSourceKind.NATIVE,
        disposition=InvoiceSyncDisposition.BLOCKED,
        projection_fingerprint=_fingerprint("nan-amounts"),
        digest_version=SUPPORTED_DIGEST_VERSION,
        issues=(
            InvoiceSyncIssueEvidence(
                code=InvoiceSyncIssueCode.HEADER_TAX_MISMATCH,
                source_line_id=same_line_id,
                expected_amount=Decimal("NaN"),
                actual_amount=Decimal("NaN"),
            ),
            InvoiceSyncIssueEvidence(
                code=InvoiceSyncIssueCode.HEADER_TAX_MISMATCH,
                source_line_id=same_line_id,
                expected_amount=Decimal("NaN"),
                actual_amount=Decimal("NaN"),
            ),
        ),
    )

    # A domain-level rejection (genuinely tied evidence), never a raw
    # TypeError escaping the sort.
    with pytest.raises(InvoiceSyncOutcomeError, match="duplicate issue evidence"):
        record_invoice_sync_outcome(db_session, command)
