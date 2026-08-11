"""The batch-run record, and the one property that makes it worth having.

`BatchOperation` shipped in January 2026 with a model, a table and a migration
and was never read or written. These tests pin the writer's contract — above
all that a failed run still leaves a record, because the run that most needs
explaining is exactly the one that would otherwise vanish with its rollback.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from app.models.batch_operation import BatchOperationStatus, BatchOperationType
from app.services.batch_operation import (
    BatchOperationService,
    BatchTally,
    batch_operation,
)

ORG = uuid.uuid4()
ACTOR = uuid.uuid4()


def _db():
    """A session double that records the order of commits and rollbacks."""
    db = MagicMock()
    db.calls = []
    db.commit.side_effect = lambda: db.calls.append("commit")
    db.rollback.side_effect = lambda: db.calls.append("rollback")
    db.add.side_effect = lambda obj: db.calls.append("add")
    db.refresh.side_effect = lambda obj: None
    return db


def _start(db):
    return BatchOperationService.start(
        db,
        organization_id=ORG,
        operation_type=BatchOperationType.SCRIPT,
        operation_name="probe",
        started_by_id=ACTOR,
    )


# --------------------------------------------------------------------------
# The record exists before the work does
# --------------------------------------------------------------------------


def test_start_commits_the_running_row_before_any_work():
    """A process killed mid-run must leave a visibly stuck RUNNING row rather
    than silence. That requires the INSERT to be committed up front."""
    db = _db()
    record = _start(db)
    assert record.status is BatchOperationStatus.RUNNING
    assert db.calls == ["add", "commit"]


def test_start_requires_an_explicit_organization():
    """Never inferred from ambient state — these are the runs that
    historically executed with no tenant scope at all."""
    with pytest.raises(TypeError):
        BatchOperationService.start(
            _db(),
            operation_type=BatchOperationType.SCRIPT,
            operation_name="probe",
            started_by_id=ACTOR,
        )


# --------------------------------------------------------------------------
# Failure — the load-bearing behaviour
# --------------------------------------------------------------------------


def test_a_failing_body_still_leaves_a_record():
    db = _db()
    with pytest.raises(ValueError):
        with batch_operation(
            db,
            organization_id=ORG,
            operation_type=BatchOperationType.SCRIPT,
            operation_name="probe",
            started_by_id=ACTOR,
        ):
            raise ValueError("boom")
    # add+commit for RUNNING, then rollback the work, then commit the failure.
    assert db.calls == ["add", "commit", "rollback", "add", "commit"]


def test_the_work_is_rolled_back_before_the_failure_is_committed():
    """Order matters. Committing the failure without rolling back first would
    persist the very changes the failure should have undone."""
    db = _db()
    with pytest.raises(RuntimeError):
        with batch_operation(
            db,
            organization_id=ORG,
            operation_type=BatchOperationType.SCRIPT,
            operation_name="probe",
            started_by_id=ACTOR,
        ):
            raise RuntimeError("boom")
    tail = db.calls[2:]
    assert tail[0] == "rollback", "work must be rolled back first"
    assert tail[-1] == "commit"


def test_the_original_exception_is_re_raised_unchanged():
    """This records failures; it never swallows them."""
    sentinel = KeyError("the-original")
    db = _db()
    with pytest.raises(KeyError) as caught:
        with batch_operation(
            db,
            organization_id=ORG,
            operation_type=BatchOperationType.SCRIPT,
            operation_name="probe",
            started_by_id=ACTOR,
        ):
            raise sentinel
    assert caught.value is sentinel


def test_the_error_message_names_the_exception_type():
    db = _db()
    record = _start(db)
    BatchOperationService.fail(db, record, "ValueError: boom")
    assert record.status is BatchOperationStatus.FAILED
    assert "ValueError" in record.error_message


# --------------------------------------------------------------------------
# Success
# --------------------------------------------------------------------------


def test_a_successful_run_records_its_counts():
    db = _db()
    with batch_operation(
        db,
        organization_id=ORG,
        operation_type=BatchOperationType.IMPORT,
        operation_name="probe",
        started_by_id=ACTOR,
    ) as tally:
        tally.created = 5
        tally.updated = 2
        tally.skipped = 1
        tally.failed = 0

    assert db.calls == ["add", "commit", "commit"]


def test_tally_tracks_entities_by_type():
    tally = BatchTally()
    a, b = uuid.uuid4(), uuid.uuid4()
    tally.track("invoice", a)
    tally.track("invoice", b)
    tally.track("payment", a)
    assert tally.entities == {
        "invoice": [str(a), str(b)],
        "payment": [str(a)],
    }


def test_complete_writes_tracked_entities_onto_the_record():
    db = _db()
    record = _start(db)
    tally = BatchTally(created=1)
    entity = uuid.uuid4()
    tally.track("invoice", entity)
    BatchOperationService.complete(db, record, tally)
    assert record.created_entity_ids == {"invoice": [str(entity)]}
    assert record.status is BatchOperationStatus.COMPLETED


# --------------------------------------------------------------------------
# Identifying the input, not just naming it
# --------------------------------------------------------------------------


def test_the_checksum_and_metadata_reach_the_record():
    """`source_file` says which files a run CLAIMED to read; `source_checksum`
    says what it actually read. Both columns shipped in January 2026 with no
    writer — these are the arguments that make them real."""
    db = _db()
    record = BatchOperationService.start(
        db,
        organization_id=ORG,
        operation_type=BatchOperationType.IMPORT,
        operation_name="import_uba_statements",
        started_by_id=ACTOR,
        source_file="/statements/uba",
        source_checksum="deadbeef" * 8,
        metadata={"bank": "033", "files": {"a.xlsx": "abc"}},
    )
    assert record.source_checksum == "deadbeef" * 8
    assert record.metadata_ == {"bank": "033", "files": {"a.xlsx": "abc"}}


def test_the_checksum_survives_the_context_manager():
    """The passthrough that matters: importers call `batch_operation`, not
    `start` directly, so an argument dropped in the wrapper would be silently
    lost rather than rejected."""
    db = _db()
    with batch_operation(
        db,
        organization_id=ORG,
        operation_type=BatchOperationType.IMPORT,
        operation_name="import_zenith_statements",
        started_by_id=ACTOR,
        source_checksum="c0ffee" * 8,
    ):
        pass
    record = db.add.call_args[0][0]
    assert record.source_checksum == "c0ffee" * 8
