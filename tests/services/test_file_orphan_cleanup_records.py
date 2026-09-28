"""Durable run/deletion record canaries for the orphan cleanup task.

These tests cover the four recording functions in
``app.services.file_object_cleanup`` directly against a bespoke, hand-created
SQLite schema (mirroring ``tests/services/test_file_object_cleanup.py``'s
``_session()`` pattern) rather than the shared ``db_session`` fixture's
engine, because ``FileOrphanCleanupRun.outcome_counts`` is a Postgres
``JSONB`` column that ``Base.metadata.create_all`` cannot compile for SQLite
(the same reason the shared ``SQLITE_COMPATIBLE_TABLES`` allowlist excludes
JSONB-bearing models). ``app.tasks.file_object_reconciliation
.clean_tenant_file_objects`` is the real caller of all four; see
``tests/services/test_file_object_reconciliation.py`` for the task-level
recording-order and failure-mode canaries.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.services.file_object_cleanup import (
    OrphanCleanupPlan,
    record_cleanup_key_outcome,
    record_cleanup_keys_planned,
    record_cleanup_run_finished,
    record_cleanup_run_started,
)


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    connection = engine.connect()
    connection.execute(text("ATTACH DATABASE ':memory:' AS public"))
    connection.execute(
        text(
            "CREATE TABLE public.file_orphan_cleanup_runs ("
            "id TEXT PRIMARY KEY, organization_id TEXT, plan_digest TEXT, "
            "older_than TEXT, plan_observed_at TEXT, provider_code TEXT, "
            "candidate_count INTEGER, actor TEXT, invocation_id TEXT, "
            "status TEXT, started_at TEXT, finished_at TEXT, outcome_counts TEXT)"
        )
    )
    connection.execute(
        text(
            "CREATE TABLE public.file_orphan_cleanup_deletions ("
            "id TEXT PRIMARY KEY, organization_id TEXT, run_id TEXT, "
            "storage_key TEXT, key_digest TEXT, outcome TEXT, error_class TEXT, "
            "observed_last_modified TEXT, recorded_at TEXT)"
        )
    )
    return Session(bind=connection)


def _plan(*, tenant_id, candidate_keys=("k1",)) -> OrphanCleanupPlan:
    older_than = datetime(2026, 9, 1, tzinfo=UTC)
    return OrphanCleanupPlan(
        scope=None,
        scope_kind="tenant",
        tenant_id=str(tenant_id),
        provider_code="erp_s3",
        older_than=older_than,
        candidate_keys=candidate_keys,
        max_deletions=100,
        managed_objects=len(candidate_keys),
        referenced_objects=0,
        in_flight_unreferenced=0,
        old_managed_objects=len(candidate_keys),
        old_referenced_objects=0,
        missing_references=0,
        boundary_drift=0,
        old_boundary_drift=0,
        plan_observed_at=datetime(2026, 9, 20, tzinfo=UTC),
        plan_digest="a" * 64,
    )


def test_record_cleanup_run_started_persists_the_reviewed_plan_identity() -> None:
    db = _session()
    tenant_id = uuid4()
    plan = _plan(tenant_id=tenant_id, candidate_keys=("k1", "k2"))

    run_id = record_cleanup_run_started(
        db,
        tenant_id=tenant_id,
        plan=plan,
        actor="operator@example.com",
        invocation_id="task-1",
    )
    db.flush()

    row = db.execute(
        text(
            "SELECT organization_id, plan_digest, provider_code, "
            "candidate_count, actor, invocation_id, status "
            "FROM public.file_orphan_cleanup_runs WHERE id = :id"
        ),
        {"id": str(run_id)},
    ).one()
    assert row.organization_id == str(tenant_id)
    assert row.plan_digest == plan.plan_digest
    assert row.provider_code == "erp_s3"
    assert row.candidate_count == 2
    assert row.actor == "operator@example.com"
    assert row.invocation_id == "task-1"
    assert row.status == "running"


def test_record_cleanup_run_started_refuses_an_empty_actor_or_invocation_id() -> None:
    db = _session()
    tenant_id = uuid4()
    plan = _plan(tenant_id=tenant_id)

    with pytest.raises(ValueError):
        record_cleanup_run_started(
            db, tenant_id=tenant_id, plan=plan, actor="", invocation_id="task-1"
        )
    with pytest.raises(ValueError):
        record_cleanup_run_started(
            db,
            tenant_id=tenant_id,
            plan=plan,
            actor="operator@example.com",
            invocation_id="",
        )


def test_record_cleanup_keys_planned_inserts_one_planned_row_per_key() -> None:
    db = _session()
    tenant_id = uuid4()
    plan = _plan(tenant_id=tenant_id, candidate_keys=("k1", "k2"))
    run_id = record_cleanup_run_started(
        db,
        tenant_id=tenant_id,
        plan=plan,
        actor="operator@example.com",
        invocation_id="task-1",
    )

    record_cleanup_keys_planned(
        db, tenant_id=tenant_id, run_id=run_id, keys=plan.candidate_keys
    )
    db.flush()

    rows = db.execute(
        text(
            "SELECT storage_key, key_digest, outcome "
            "FROM public.file_orphan_cleanup_deletions "
            "WHERE run_id = :run_id ORDER BY storage_key"
        ),
        {"run_id": str(run_id)},
    ).all()
    assert [row.storage_key for row in rows] == ["k1", "k2"]
    assert [row.outcome for row in rows] == ["planned", "planned"]
    assert rows[0].key_digest == hashlib.sha256(b"k1").hexdigest()


def test_record_cleanup_key_outcome_updates_the_planned_row_in_place() -> None:
    """The raw key is durable from the planned insert; recording the
    terminal outcome UPDATEs that same row rather than inserting a new one —
    a key's planned-then-final history is exactly one row throughout."""
    db = _session()
    tenant_id = uuid4()
    plan = _plan(tenant_id=tenant_id)
    run_id = record_cleanup_run_started(
        db,
        tenant_id=tenant_id,
        plan=plan,
        actor="operator@example.com",
        invocation_id="task-1",
    )
    record_cleanup_keys_planned(
        db, tenant_id=tenant_id, run_id=run_id, keys=("tenants/x/files/y",)
    )
    db.flush()

    record_cleanup_key_outcome(
        db,
        tenant_id=tenant_id,
        run_id=run_id,
        storage_key="tenants/x/files/y",
        outcome="deleted",
    )

    rows = db.execute(
        text(
            "SELECT storage_key, key_digest, outcome, error_class "
            "FROM public.file_orphan_cleanup_deletions WHERE run_id = :run_id"
        ),
        {"run_id": str(run_id)},
    ).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.storage_key == "tenants/x/files/y"
    assert row.key_digest == hashlib.sha256(b"tenants/x/files/y").hexdigest()
    assert row.outcome == "deleted"
    assert row.error_class is None


def test_record_cleanup_key_outcome_raises_when_no_planned_row_exists() -> None:
    """Recording an outcome before planning ran (or for a key never planned)
    must raise, not silently insert -- a durable row's raw key must always
    have existed before any delete could have happened."""
    db = _session()
    tenant_id = uuid4()
    plan = _plan(tenant_id=tenant_id)
    run_id = record_cleanup_run_started(
        db,
        tenant_id=tenant_id,
        plan=plan,
        actor="operator@example.com",
        invocation_id="task-1",
    )

    with pytest.raises(ValueError, match="no planned deletion row"):
        record_cleanup_key_outcome(
            db,
            tenant_id=tenant_id,
            run_id=run_id,
            storage_key="never-planned",
            outcome="deleted",
        )


def test_record_cleanup_key_outcome_rejects_an_outcome_outside_the_vocabulary() -> None:
    db = _session()
    tenant_id = uuid4()
    plan = _plan(tenant_id=tenant_id)
    run_id = record_cleanup_run_started(
        db,
        tenant_id=tenant_id,
        plan=plan,
        actor="operator@example.com",
        invocation_id="task-1",
    )
    record_cleanup_keys_planned(db, tenant_id=tenant_id, run_id=run_id, keys=("k1",))
    db.flush()

    with pytest.raises(ValueError):
        record_cleanup_key_outcome(
            db,
            tenant_id=tenant_id,
            run_id=run_id,
            storage_key="k1",
            outcome="not_a_real_outcome",
        )


def test_record_cleanup_key_outcome_rejects_planned_as_a_terminal_outcome() -> None:
    """``planned`` is the pre-delete intent state, never a terminal outcome a
    caller may report back through ``record_cleanup_key_outcome``."""
    db = _session()
    tenant_id = uuid4()
    plan = _plan(tenant_id=tenant_id)
    run_id = record_cleanup_run_started(
        db,
        tenant_id=tenant_id,
        plan=plan,
        actor="operator@example.com",
        invocation_id="task-1",
    )
    record_cleanup_keys_planned(db, tenant_id=tenant_id, run_id=run_id, keys=("k1",))
    db.flush()

    with pytest.raises(ValueError):
        record_cleanup_key_outcome(
            db,
            tenant_id=tenant_id,
            run_id=run_id,
            storage_key="k1",
            outcome="planned",
        )


def test_record_cleanup_run_finished_sets_terminal_status_and_counts() -> None:
    db = _session()
    tenant_id = uuid4()
    plan = _plan(tenant_id=tenant_id)
    run_id = record_cleanup_run_started(
        db,
        tenant_id=tenant_id,
        plan=plan,
        actor="operator@example.com",
        invocation_id="task-1",
    )
    db.flush()

    # The finish path SELECTs the run by (id, tenant); the org-filter
    # listener (app/db/org_listener.py), when registered on the process-wide
    # Session class by an earlier test, requires this priming regardless of
    # which engine the session is bound to — set it explicitly rather than
    # relying on collection-order-dependent listener state.
    db.info["organization_id"] = tenant_id

    record_cleanup_run_finished(
        db,
        tenant_id=tenant_id,
        run_id=run_id,
        status="completed",
        outcome_counts={"deleted": 1},
    )
    db.flush()

    row = db.execute(
        text(
            "SELECT status, outcome_counts, finished_at "
            "FROM public.file_orphan_cleanup_runs WHERE id = :id"
        ),
        {"id": str(run_id)},
    ).one()
    assert row.status == "completed"
    assert row.finished_at is not None


def test_record_cleanup_run_finished_refuses_an_unknown_status() -> None:
    db = _session()
    tenant_id = uuid4()
    plan = _plan(tenant_id=tenant_id)
    run_id = record_cleanup_run_started(
        db,
        tenant_id=tenant_id,
        plan=plan,
        actor="operator@example.com",
        invocation_id="task-1",
    )
    db.info["organization_id"] = tenant_id

    with pytest.raises(ValueError):
        record_cleanup_run_finished(
            db,
            tenant_id=tenant_id,
            run_id=run_id,
            status="bogus",
            outcome_counts={},
        )


def test_record_cleanup_run_finished_refuses_a_run_owned_by_another_tenant() -> None:
    db = _session()
    tenant_id = uuid4()
    other_tenant_id = uuid4()
    plan = _plan(tenant_id=tenant_id)
    run_id = record_cleanup_run_started(
        db,
        tenant_id=tenant_id,
        plan=plan,
        actor="operator@example.com",
        invocation_id="task-1",
    )
    db.info["organization_id"] = other_tenant_id

    with pytest.raises(ValueError):
        record_cleanup_run_finished(
            db,
            tenant_id=other_tenant_id,
            run_id=run_id,
            status="completed",
            outcome_counts={},
        )
