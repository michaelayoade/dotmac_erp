"""Plan digest, drift, and cap-authorization canaries for orphan cleanup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_files import ObjectInfo, list_objects
from dotmac_kernel.cache import TenantScope
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.services.file_object_cleanup import (
    MAX_DELETIONS_CEILING,
    CleanupCapExceeded,
    CleanupPlanDrift,
    authorize_apply,
    plan_orphan_cleanup,
)
from app.services.file_object_reconciliation import (
    ObjectReconciliationReport,
    report_file_objects,
)


def _report(
    *, candidate_keys: tuple[str, ...], older_than: datetime, scope: object = None
) -> ObjectReconciliationReport:
    return ObjectReconciliationReport(
        scope=scope if scope is not None else TenantScope(uuid4()),
        provider_code="erp_s3",
        older_than=older_than,
        objects=(),
        candidate_keys=candidate_keys,
        missing_references=(),
        boundary_drift=(),
    )


def test_plan_digest_is_independent_of_candidate_observation_order() -> None:
    older_than = datetime(2026, 9, 24, tzinfo=UTC)
    tenant_id = uuid4()
    scope = TenantScope(tenant_id)
    keys = (f"tenants/{tenant_id}/files/b", f"tenants/{tenant_id}/files/a")
    reversed_keys = tuple(reversed(keys))

    plan_a = plan_orphan_cleanup(
        _report(candidate_keys=keys, older_than=older_than, scope=scope)
    )
    plan_b = plan_orphan_cleanup(
        _report(candidate_keys=reversed_keys, older_than=older_than, scope=scope)
    )

    assert plan_a.plan_digest == plan_b.plan_digest
    assert plan_a.candidate_keys == plan_b.candidate_keys == tuple(sorted(keys))


def test_digest_drifts_when_a_candidate_key_is_added_or_removed() -> None:
    older_than = datetime(2026, 9, 24, tzinfo=UTC)
    scope = TenantScope(uuid4())
    base = plan_orphan_cleanup(
        _report(candidate_keys=("k1", "k2"), older_than=older_than, scope=scope)
    )
    added = plan_orphan_cleanup(
        _report(candidate_keys=("k1", "k2", "k3"), older_than=older_than, scope=scope)
    )
    removed = plan_orphan_cleanup(
        _report(candidate_keys=("k1",), older_than=older_than, scope=scope)
    )

    assert base.plan_digest != added.plan_digest
    assert base.plan_digest != removed.plan_digest

    with pytest.raises(CleanupPlanDrift):
        authorize_apply(added, expected_plan_digest=base.plan_digest)


def test_digest_drifts_when_older_than_changes() -> None:
    scope = TenantScope(uuid4())
    plan_a = plan_orphan_cleanup(
        _report(
            candidate_keys=("k1",),
            older_than=datetime(2026, 9, 24, tzinfo=UTC),
            scope=scope,
        )
    )
    plan_b = plan_orphan_cleanup(
        _report(
            candidate_keys=("k1",),
            older_than=datetime(2026, 9, 25, tzinfo=UTC),
            scope=scope,
        )
    )

    assert plan_a.plan_digest != plan_b.plan_digest
    with pytest.raises(CleanupPlanDrift):
        authorize_apply(plan_b, expected_plan_digest=plan_a.plan_digest)


def test_authorize_apply_refuses_when_candidates_exceed_the_cap() -> None:
    older_than = datetime(2026, 9, 24, tzinfo=UTC)
    report = _report(candidate_keys=("k1", "k2", "k3"), older_than=older_than)
    plan = plan_orphan_cleanup(report, max_deletions=2)

    with pytest.raises(CleanupCapExceeded):
        authorize_apply(plan, expected_plan_digest=plan.plan_digest)


def test_plan_orphan_cleanup_refuses_a_cap_above_the_ceiling() -> None:
    older_than = datetime(2026, 9, 24, tzinfo=UTC)
    report = _report(candidate_keys=(), older_than=older_than)

    with pytest.raises(CleanupCapExceeded):
        plan_orphan_cleanup(report, max_deletions=MAX_DELETIONS_CEILING + 1)


def test_plan_orphan_cleanup_refuses_a_non_positive_cap() -> None:
    older_than = datetime(2026, 9, 24, tzinfo=UTC)
    report = _report(candidate_keys=(), older_than=older_than)

    with pytest.raises(CleanupCapExceeded):
        plan_orphan_cleanup(report, max_deletions=0)


def test_authorize_apply_accepts_a_matching_digest_within_the_cap() -> None:
    older_than = datetime(2026, 9, 24, tzinfo=UTC)
    report = _report(candidate_keys=("k1", "k2"), older_than=older_than)
    plan = plan_orphan_cleanup(report, max_deletions=10)

    authorize_apply(plan, expected_plan_digest=plan.plan_digest)


class _ListingProvider:
    code = "erp_s3"

    def __init__(self, objects: tuple[ObjectInfo, ...]) -> None:
        self.objects = objects

    def list(self, prefix: str):
        return (item for item in self.objects if item.key.startswith(prefix))


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    connection = engine.connect()
    connection.execute(text("ATTACH DATABASE ':memory:' AS mod_files"))
    connection.execute(
        text(
            "CREATE TABLE mod_files.stored_files (tenant_id CHAR(32), "
            "provider_code VARCHAR(32), storage_key VARCHAR(500), "
            "state VARCHAR(32), created_at DATETIME)"
        )
    )
    return Session(bind=connection)


def test_referenced_and_in_grace_objects_never_reach_the_plan() -> None:
    now = datetime(2026, 9, 27, tzinfo=UTC)
    tenant_id = uuid4()
    prefix = f"tenants/{tenant_id}/files/"
    referenced = prefix + str(uuid4())
    in_flight = prefix + str(uuid4())
    old_orphan = prefix + str(uuid4())
    scope = TenantScope(tenant_id)
    provider = _ListingProvider(
        (
            ObjectInfo(referenced, 10, now - timedelta(days=3)),
            ObjectInfo(in_flight, 12, now - timedelta(minutes=5)),
            ObjectInfo(old_orphan, 11, now - timedelta(days=3)),
        )
    )
    observations = list_objects(provider, scope=scope)
    db = _session()
    db.execute(
        text(
            "INSERT INTO mod_files.stored_files "
            "(tenant_id, provider_code, storage_key, state, created_at) "
            "VALUES (:tenant, 'erp_s3', :key, 'available', :created_at)"
        ),
        {
            "tenant": tenant_id.hex,
            "key": referenced,
            "created_at": "2026-09-20 00:00:00.000000",
        },
    )

    report = report_file_objects(
        db,
        scope=scope,
        provider_code=provider.code,
        observations=observations,
        observed_at=now,
    )
    plan = plan_orphan_cleanup(report)

    assert plan.candidate_keys == (old_orphan,)
    assert referenced not in plan.candidate_keys
    assert in_flight not in plan.candidate_keys
