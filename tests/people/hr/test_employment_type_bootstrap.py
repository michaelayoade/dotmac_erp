from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.models.people.hr import EmploymentType
from app.services.people.hr.employment_type_bootstrap import (
    BootstrapMode,
    EmploymentTypeBootstrapError,
    EmploymentTypeBootstrapService,
    EmploymentTypeReconcileResult,
    EmploymentTypeSourceRecord,
    EmploymentTypeTargetRecord,
    fingerprint_set_digest,
)

ORG_ID = UUID("00000000-0000-0000-0000-000000000101")
FIRST_ID = UUID("00000000-0000-0000-0000-000000000201")
SECOND_ID = UUID("00000000-0000-0000-0000-000000000202")
EXTRA_ID = UUID("00000000-0000-0000-0000-000000000299")
NOW = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)


def test_legacy_timestamp_mapping_matches_the_timestamptz_source() -> None:
    """Bootstrap evidence must not discard the source columns' UTC offsets."""
    assert EmploymentType.__table__.c.created_at.type.timezone is True
    assert EmploymentType.__table__.c.updated_at.type.timezone is True


def _source(
    record_id: UUID,
    *,
    fingerprint: str = "a" * 64,
    updated_at: datetime | None = NOW,
) -> EmploymentTypeSourceRecord:
    return EmploymentTypeSourceRecord(
        source_id=record_id,
        source_fingerprint=fingerprint,
        source_created_at=NOW - timedelta(days=1),
        source_updated_at=updated_at,
        code=f"TYPE-{str(record_id)[-3:]}",
        name=f"Type {str(record_id)[-3:]}",
        description=None,
        is_active=True,
    )


def _target_fingerprint(source: EmploymentTypeSourceRecord) -> str:
    encoded = (
        f"{source.source_id}|{source.code}|{source.name}|"
        f"{source.description}|{source.is_active}"
    ).encode()
    return f"et1:{hashlib.sha256(encoded).hexdigest()}"


class _Source:
    def __init__(self, *scans: tuple[EmploymentTypeSourceRecord, ...]):
        self.scans = list(scans)
        self.calls = 0
        self.fence_calls = 0

    def fence(self) -> None:
        assert self.calls == 0, "the source must be fenced before its first page"
        self.fence_calls += 1

    def scan(self, *, page_size: int) -> tuple[EmploymentTypeSourceRecord, ...]:
        assert self.fence_calls == 1
        assert 1 <= page_size <= 200
        index = min(self.calls, len(self.scans) - 1)
        self.calls += 1
        return self.scans[index]


class _Target:
    def __init__(self, records: dict[UUID, str] | None = None):
        self.records = dict(records or {})
        self.scan_calls = 0
        self.corrupt_after = False

    def scan(self, *, page_size: int) -> tuple[EmploymentTypeTargetRecord, ...]:
        assert 1 <= page_size <= 200
        self.scan_calls += 1
        records = dict(self.records)
        if self.corrupt_after and self.scan_calls > 1 and records:
            first_id = min(records, key=lambda value: value.bytes)
            records[first_id] = "et1:" + "f" * 64
        return tuple(
            EmploymentTypeTargetRecord(target_id=record_id, target_fingerprint=value)
            for record_id, value in sorted(
                records.items(), key=lambda item: item[0].bytes
            )
        )

    def reconcile(
        self, source: EmploymentTypeSourceRecord
    ) -> EmploymentTypeReconcileResult:
        fingerprint = _target_fingerprint(source)
        if source.source_id not in self.records:
            action = "CREATED"
        elif self.records[source.source_id] != fingerprint:
            action = "UPDATED"
        else:
            action = "UNCHANGED"
        self.records[source.source_id] = fingerprint
        return EmploymentTypeReconcileResult(
            action=action,
            target_id=source.source_id,
            source_fingerprint=source.source_fingerprint,
            target_fingerprint=fingerprint,
        )


def _service(source: _Source, target: _Target) -> EmploymentTypeBootstrapService:
    db = SimpleNamespace(info={"organization_id": ORG_ID, "tenant_id": ORG_ID})
    return EmploymentTypeBootstrapService(  # type: ignore[arg-type]
        db,
        organization_id=ORG_ID,
        source=source,
        target=target,
    )


def test_fingerprint_set_digest_is_order_independent_and_domain_separated() -> None:
    forward = [(FIRST_ID, "a" * 64), (SECOND_ID, "b" * 64)]
    target = [(record_id, f"et1:{fingerprint}") for record_id, fingerprint in forward]
    assert fingerprint_set_digest(kind="source", items=forward) == (
        fingerprint_set_digest(kind="source", items=reversed(forward))
    )
    assert fingerprint_set_digest(kind="source", items=forward) != (
        fingerprint_set_digest(kind="target", items=target)
    )


def test_fingerprint_set_digest_refuses_the_wrong_fingerprint_vocabulary() -> None:
    with pytest.raises(EmploymentTypeBootstrapError, match="invalid fingerprint"):
        fingerprint_set_digest(kind="target", items=[(FIRST_ID, "a" * 64)])


def test_initial_commit_reconciles_all_rows_and_returns_both_set_digests() -> None:
    records = (_source(SECOND_ID, fingerprint="b" * 64), _source(FIRST_ID))
    source = _Source(records, tuple(reversed(records)))
    target = _Target()

    result = _service(source, target).execute(mode=BootstrapMode.COMMIT, page_size=1)

    assert source.fence_calls == 1
    assert source.calls == 2
    assert result.source_count == result.target_after_count == 2
    assert result.target_before_count == 0
    assert (result.created, result.updated, result.unchanged) == (2, 0, 0)
    assert result.source_fingerprint_set_digest.startswith("sha256:")
    assert result.target_after_fingerprint_set_digest.startswith("sha256:")


def test_dry_run_can_preview_an_established_target_without_replay_semantics() -> None:
    record = _source(FIRST_ID)
    target = _Target({FIRST_ID: _target_fingerprint(record)})

    result = _service(_Source((record,), (record,)), target).execute(
        mode=BootstrapMode.DRY_RUN
    )

    assert result.mode is BootstrapMode.DRY_RUN
    assert (result.created, result.updated, result.unchanged) == (0, 0, 1)


def test_initial_commit_refuses_an_established_target() -> None:
    record = _source(FIRST_ID)
    with pytest.raises(EmploymentTypeBootstrapError, match="initial-bootstrap only"):
        _service(
            _Source((record,)), _Target({FIRST_ID: _target_fingerprint(record)})
        ).execute(mode=BootstrapMode.COMMIT)


def test_replay_refuses_an_empty_target() -> None:
    record = _source(FIRST_ID)
    with pytest.raises(EmploymentTypeBootstrapError, match="established non-empty"):
        _service(_Source((record,)), _Target()).execute(mode=BootstrapMode.REPLAY)


def test_replay_accepts_a_quiescent_empty_source_and_target() -> None:
    source = _Source((), ())

    result = _service(source, _Target()).execute(mode=BootstrapMode.REPLAY)

    assert source.fence_calls == 1
    assert source.calls == 2
    assert result.source_count == 0
    assert result.target_before_count == result.target_after_count == 0
    assert (result.created, result.updated, result.unchanged) == (0, 0, 0)
    assert result.target_before_fingerprint_set_digest == (
        result.target_after_fingerprint_set_digest
    )


def test_replay_updates_existing_and_creates_new_source_rows() -> None:
    first = _source(FIRST_ID)
    second = _source(SECOND_ID, fingerprint="b" * 64)
    target = _Target({FIRST_ID: "et1:" + "0" * 64})

    result = _service(_Source((first, second), (second, first)), target).execute(
        mode=BootstrapMode.REPLAY
    )

    assert (result.created, result.updated, result.unchanged) == (1, 1, 0)


def test_target_row_absent_from_complete_source_is_refused() -> None:
    record = _source(FIRST_ID)
    target = _Target(
        {
            FIRST_ID: _target_fingerprint(record),
            EXTRA_ID: "et1:" + "e" * 64,
        }
    )
    with pytest.raises(EmploymentTypeBootstrapError, match="absent from the complete"):
        _service(_Source((record,)), target).execute(mode=BootstrapMode.REPLAY)


def test_second_complete_source_scan_detects_timestamp_only_change() -> None:
    first = _source(FIRST_ID)
    changed = _source(FIRST_ID, updated_at=NOW + timedelta(seconds=1))
    with pytest.raises(EmploymentTypeBootstrapError, match="two complete scans"):
        _service(_Source((first,), (changed,)), _Target()).execute(
            mode=BootstrapMode.COMMIT
        )


def test_post_reconciliation_target_fingerprint_must_match_outcome() -> None:
    record = _source(FIRST_ID)
    target = _Target()
    target.corrupt_after = True
    with pytest.raises(EmploymentTypeBootstrapError, match="fingerprint changed"):
        _service(_Source((record,), (record,)), target).execute(
            mode=BootstrapMode.COMMIT
        )


def test_service_refuses_a_partially_primed_session() -> None:
    db = SimpleNamespace(info={"organization_id": ORG_ID})
    with pytest.raises(EmploymentTypeBootstrapError, match="mapped People tenant"):
        EmploymentTypeBootstrapService(  # type: ignore[arg-type]
            db,
            organization_id=ORG_ID,
            source=_Source((_source(FIRST_ID),)),
            target=_Target(),
        )
