"""Plan digest, drift, safety, and expiry canaries for orphan cleanup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_files import FileState, ObjectInfo, list_objects
from dotmac_kernel.cache import PlatformScope, TenantScope
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.services.file_object_cleanup import (
    MAX_DELETIONS_CEILING,
    MAX_PLAN_AGE_HOURS,
    MIN_APPLY_GRACE_HOURS,
    CleanupCapExceeded,
    CleanupPartialFailure,
    CleanupPlanDrift,
    CleanupPlanExpired,
    CleanupUnsafeReferenceView,
    authorize_apply,
    is_storage_key_referenced,
    plan_orphan_cleanup,
)
from app.services.file_object_reconciliation import (
    BoundaryDrift,
    ObjectEvidence,
    ObjectReconciliationReport,
    report_file_objects,
)


def _report(
    *,
    candidate_keys: tuple[str, ...],
    older_than: datetime,
    scope: object = None,
    objects: tuple[ObjectEvidence, ...] = (),
    boundary_drift: tuple[BoundaryDrift, ...] = (),
) -> ObjectReconciliationReport:
    return ObjectReconciliationReport(
        scope=scope if scope is not None else TenantScope(uuid4()),
        provider_code="erp_s3",
        older_than=older_than,
        objects=objects,
        candidate_keys=candidate_keys,
        missing_references=(),
        boundary_drift=boundary_drift,
    )


def _observed_at_for(older_than: datetime) -> datetime:
    """A REAL observed_at consistent with the default retention window."""
    return older_than + timedelta(hours=MIN_APPLY_GRACE_HOURS)


def test_plan_digest_is_independent_of_candidate_observation_order() -> None:
    older_than = datetime(2026, 9, 24, tzinfo=UTC)
    observed_at = _observed_at_for(older_than)
    tenant_id = uuid4()
    scope = TenantScope(tenant_id)
    keys = (f"tenants/{tenant_id}/files/b", f"tenants/{tenant_id}/files/a")
    reversed_keys = tuple(reversed(keys))

    plan_a = plan_orphan_cleanup(
        _report(candidate_keys=keys, older_than=older_than, scope=scope), observed_at
    )
    plan_b = plan_orphan_cleanup(
        _report(candidate_keys=reversed_keys, older_than=older_than, scope=scope),
        observed_at,
    )

    assert plan_a.plan_digest == plan_b.plan_digest
    assert plan_a.candidate_keys == plan_b.candidate_keys == tuple(sorted(keys))


def test_digest_drifts_when_a_candidate_key_is_added_or_removed() -> None:
    older_than = datetime(2026, 9, 24, tzinfo=UTC)
    observed_at = _observed_at_for(older_than)
    scope = TenantScope(uuid4())
    base = plan_orphan_cleanup(
        _report(candidate_keys=("k1", "k2"), older_than=older_than, scope=scope),
        observed_at,
    )
    added = plan_orphan_cleanup(
        _report(candidate_keys=("k1", "k2", "k3"), older_than=older_than, scope=scope),
        observed_at,
    )
    removed = plan_orphan_cleanup(
        _report(candidate_keys=("k1",), older_than=older_than, scope=scope),
        observed_at,
    )

    assert base.plan_digest != added.plan_digest
    assert base.plan_digest != removed.plan_digest

    with pytest.raises(CleanupPlanDrift):
        authorize_apply(
            added, expected_plan_digest=base.plan_digest, now=added.plan_observed_at
        )


def test_digest_drifts_when_older_than_changes() -> None:
    scope = TenantScope(uuid4())
    older_than_a = datetime(2026, 9, 24, tzinfo=UTC)
    older_than_b = datetime(2026, 9, 25, tzinfo=UTC)
    plan_a = plan_orphan_cleanup(
        _report(candidate_keys=("k1",), older_than=older_than_a, scope=scope),
        _observed_at_for(older_than_a),
    )
    plan_b = plan_orphan_cleanup(
        _report(candidate_keys=("k1",), older_than=older_than_b, scope=scope),
        _observed_at_for(older_than_b),
    )

    assert plan_a.plan_digest != plan_b.plan_digest
    with pytest.raises(CleanupPlanDrift):
        authorize_apply(
            plan_b,
            expected_plan_digest=plan_a.plan_digest,
            now=plan_b.plan_observed_at,
        )


def test_digest_is_stable_across_a_fresh_upload_and_a_fresh_row() -> None:
    """A fresh (not-yet-old) upload, and a fresh (not-past-grace) metadata
    row, must never themselves cause CleanupPlanDrift -- only a change to
    data OLDER than the reviewed cutoff may."""
    older_than = datetime(2026, 9, 24, tzinfo=UTC)
    observed_at = _observed_at_for(older_than)
    scope = TenantScope(uuid4())
    old_orphan = ObjectEvidence(
        key="tenants/x/files/old",
        size_bytes=1,
        last_modified=older_than - timedelta(days=1),
        referenced=False,
        old_enough=True,
    )
    old_referenced = ObjectEvidence(
        key="tenants/x/files/ref",
        size_bytes=1,
        last_modified=older_than - timedelta(days=1),
        referenced=True,
        old_enough=True,
    )
    before = plan_orphan_cleanup(
        _report(
            candidate_keys=("tenants/x/files/old",),
            older_than=older_than,
            scope=scope,
            objects=(old_orphan, old_referenced),
        ),
        observed_at,
    )

    fresh_upload = ObjectEvidence(
        key="tenants/x/files/new",
        size_bytes=1,
        last_modified=observed_at - timedelta(minutes=1),
        referenced=True,  # a fresh row now references it
        old_enough=False,
    )
    after = plan_orphan_cleanup(
        _report(
            candidate_keys=("tenants/x/files/old",),
            older_than=older_than,
            scope=scope,
            objects=(old_orphan, old_referenced, fresh_upload),
        ),
        observed_at,
    )

    assert before.plan_digest == after.plan_digest
    authorize_apply(after, expected_plan_digest=before.plan_digest, now=observed_at)


def test_authorize_apply_refuses_when_candidates_exceed_the_cap() -> None:
    older_than = datetime(2026, 9, 24, tzinfo=UTC)
    observed_at = _observed_at_for(older_than)
    report = _report(candidate_keys=("k1", "k2", "k3"), older_than=older_than)
    plan = plan_orphan_cleanup(report, observed_at, max_deletions=2)

    with pytest.raises(CleanupCapExceeded):
        authorize_apply(
            plan, expected_plan_digest=plan.plan_digest, now=plan.plan_observed_at
        )


def test_plan_orphan_cleanup_refuses_a_cap_above_the_ceiling() -> None:
    older_than = datetime(2026, 9, 24, tzinfo=UTC)
    report = _report(candidate_keys=(), older_than=older_than)

    with pytest.raises(CleanupCapExceeded):
        plan_orphan_cleanup(
            report,
            _observed_at_for(older_than),
            max_deletions=MAX_DELETIONS_CEILING + 1,
        )


def test_plan_orphan_cleanup_refuses_a_non_positive_cap() -> None:
    older_than = datetime(2026, 9, 24, tzinfo=UTC)
    report = _report(candidate_keys=(), older_than=older_than)

    with pytest.raises(CleanupCapExceeded):
        plan_orphan_cleanup(report, _observed_at_for(older_than), max_deletions=0)


def test_authorize_apply_accepts_a_matching_digest_within_the_cap() -> None:
    older_than = datetime(2026, 9, 24, tzinfo=UTC)
    referenced_evidence = ObjectEvidence(
        key="tenants/x/files/ref",
        size_bytes=1,
        last_modified=older_than,
        referenced=True,
        old_enough=True,
    )
    report = _report(
        candidate_keys=("k1", "k2"),
        older_than=older_than,
        objects=(referenced_evidence,),
    )
    plan = plan_orphan_cleanup(report, _observed_at_for(older_than), max_deletions=10)

    authorize_apply(
        plan, expected_plan_digest=plan.plan_digest, now=plan.plan_observed_at
    )


def test_authorize_apply_refuses_a_scope_neither_tenant_nor_platform() -> None:
    older_than = datetime(2026, 9, 24, tzinfo=UTC)
    with pytest.raises(TypeError):
        plan_orphan_cleanup(
            _report(candidate_keys=(), older_than=older_than, scope=object()),
            _observed_at_for(older_than),
        )


def test_scope_kind_refuses_the_platform_scope() -> None:
    """Cleanup is tenant-only; the platform plane has no adapter."""
    older_than = datetime(2026, 9, 24, tzinfo=UTC)
    with pytest.raises(TypeError, match="only a tenant file scope"):
        plan_orphan_cleanup(
            _report(candidate_keys=(), older_than=older_than, scope=PlatformScope()),
            _observed_at_for(older_than),
        )


def test_authorize_apply_refuses_when_every_old_object_looks_unreferenced() -> None:
    """Old managed objects exist, none look referenced, candidates exist.

    Indistinguishable from a hidden-rows or RLS failure making a healthy
    tenant look fully orphaned; must refuse rather than delete.
    """
    older_than = datetime(2026, 9, 24, tzinfo=UTC)
    unreferenced = ObjectEvidence(
        key="tenants/x/files/orphan",
        size_bytes=1,
        last_modified=older_than - timedelta(days=1),
        referenced=False,
        old_enough=True,
    )
    report = _report(
        candidate_keys=("tenants/x/files/orphan",),
        older_than=older_than,
        objects=(unreferenced,),
    )
    plan = plan_orphan_cleanup(report, _observed_at_for(older_than))

    with pytest.raises(CleanupUnsafeReferenceView):
        authorize_apply(
            plan, expected_plan_digest=plan.plan_digest, now=plan.plan_observed_at
        )


def test_authorize_apply_accepts_when_only_a_fresh_object_is_unreferenced() -> None:
    """A fresh (not old_enough) unreferenced object must NOT trip the
    zero-reference refusal -- only OLD unreferenced objects count."""
    older_than = datetime(2026, 9, 24, tzinfo=UTC)
    observed_at = _observed_at_for(older_than)
    old_referenced = ObjectEvidence(
        key="tenants/x/files/ref",
        size_bytes=1,
        last_modified=older_than,
        referenced=True,
        old_enough=True,
    )
    fresh_unreferenced = ObjectEvidence(
        key="tenants/x/files/new",
        size_bytes=1,
        last_modified=observed_at - timedelta(minutes=1),
        referenced=False,
        old_enough=False,
    )
    report = _report(
        candidate_keys=(),
        older_than=older_than,
        objects=(old_referenced, fresh_unreferenced),
    )
    plan = plan_orphan_cleanup(report, observed_at)

    authorize_apply(plan, expected_plan_digest=plan.plan_digest, now=observed_at)


def test_authorize_apply_refuses_on_old_boundary_drift() -> None:
    older_than = datetime(2026, 9, 24, tzinfo=UTC)
    referenced_evidence = ObjectEvidence(
        key="tenants/x/files/ref",
        size_bytes=1,
        last_modified=older_than,
        referenced=True,
        old_enough=True,
    )
    drift = BoundaryDrift(
        key_digest="deadbeef", state=FileState.AVAILABLE, old_enough=True
    )
    report = _report(
        candidate_keys=(),
        older_than=older_than,
        objects=(referenced_evidence,),
        boundary_drift=(drift,),
    )
    plan = plan_orphan_cleanup(report, _observed_at_for(older_than))
    assert plan.old_boundary_drift == 1

    with pytest.raises(CleanupUnsafeReferenceView):
        authorize_apply(
            plan, expected_plan_digest=plan.plan_digest, now=plan.plan_observed_at
        )


def test_authorize_apply_refuses_on_fresh_boundary_drift_too() -> None:
    """The safety refusal reads the ALL-AGE boundary_drift count -- a fresh
    (not-yet-past-grace) drift row still refuses, even though it is excluded
    from the digest itself."""
    older_than = datetime(2026, 9, 24, tzinfo=UTC)
    referenced_evidence = ObjectEvidence(
        key="tenants/x/files/ref",
        size_bytes=1,
        last_modified=older_than,
        referenced=True,
        old_enough=True,
    )
    fresh_drift = BoundaryDrift(
        key_digest="deadbeef", state=FileState.AVAILABLE, old_enough=False
    )
    report = _report(
        candidate_keys=(),
        older_than=older_than,
        objects=(referenced_evidence,),
        boundary_drift=(fresh_drift,),
    )
    plan = plan_orphan_cleanup(report, _observed_at_for(older_than))
    assert plan.boundary_drift == 1
    assert plan.old_boundary_drift == 0

    with pytest.raises(CleanupUnsafeReferenceView):
        authorize_apply(
            plan, expected_plan_digest=plan.plan_digest, now=plan.plan_observed_at
        )


def test_authorize_apply_refuses_an_expired_plan() -> None:
    older_than = datetime(2026, 9, 24, tzinfo=UTC)
    report = _report(candidate_keys=(), older_than=older_than)
    plan = plan_orphan_cleanup(report, _observed_at_for(older_than))
    too_late = plan.plan_observed_at + timedelta(hours=MAX_PLAN_AGE_HOURS, minutes=1)

    with pytest.raises(CleanupPlanExpired):
        authorize_apply(plan, expected_plan_digest=plan.plan_digest, now=too_late)


def test_authorize_apply_accepts_a_plan_within_the_expiry_window() -> None:
    older_than = datetime(2026, 9, 24, tzinfo=UTC)
    report = _report(candidate_keys=(), older_than=older_than)
    plan = plan_orphan_cleanup(report, _observed_at_for(older_than))
    just_in_time = plan.plan_observed_at + timedelta(hours=MAX_PLAN_AGE_HOURS)

    authorize_apply(plan, expected_plan_digest=plan.plan_digest, now=just_in_time)


def test_plan_observed_at_is_the_real_observed_at_not_derived() -> None:
    """plan_observed_at is whatever the caller passes -- NOT derived from
    older_than -- so it cannot be manufactured by choosing a smaller
    dry-run grace_hours (the exact defeat this field closes)."""
    older_than = datetime(2026, 9, 24, tzinfo=UTC)
    real_observed_at = datetime(2026, 9, 24, 5, tzinfo=UTC)  # NOT older_than+168h
    plan = plan_orphan_cleanup(
        _report(candidate_keys=(), older_than=older_than), real_observed_at
    )
    assert plan.plan_observed_at == real_observed_at
    assert plan.plan_observed_at != older_than + timedelta(hours=MIN_APPLY_GRACE_HOURS)


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
    plan = plan_orphan_cleanup(report, now)

    assert plan.candidate_keys == (old_orphan,)
    assert referenced not in plan.candidate_keys
    assert in_flight not in plan.candidate_keys


def test_list_objects_consumes_a_multi_page_generator_completely() -> None:
    """The module trusts the provider's ``list`` to be a complete iterable.

    ``dotmac_files.list_objects`` performs no pagination of its own -- see
    ``physical.list_objects`` (a4): ``tuple(provider.list(prefix))``. ERP's
    provider (``DotmacFilesS3Provider.list``) fully materializes minio-py's
    ``list_objects(..., recursive=True)`` generator, and minio-py's own
    generator internally issues further ListObjectsV2 calls with a
    continuation token until the bucket is exhausted -- so completeness is a
    property of that SDK behaviour, not of ``dotmac_files``. This proves the
    consuming side: a provider whose ``list`` yields a large multi-batch
    generator is still fully drained into the returned tuple.
    """
    now = datetime(2026, 9, 27, tzinfo=UTC)
    tenant_id = uuid4()
    prefix = f"tenants/{tenant_id}/files/"
    page_size = 137
    total_pages = 3
    keys = [f"{prefix}{i:06d}" for i in range(page_size * total_pages)]

    class _PaginatingProvider:
        code = "erp_s3"

        def list(self, requested_prefix: str):
            # Yield lazily, across many logical "pages", to prove the caller
            # drains the WHOLE generator rather than an early batch.
            for key in keys:
                if key.startswith(requested_prefix):
                    yield ObjectInfo(key, 1, now - timedelta(days=10))

    observations = list_objects(_PaginatingProvider(), scope=TenantScope(tenant_id))
    assert len(observations) == len(keys)
    assert {item.key for item in observations} == set(keys)


def test_is_storage_key_referenced_true_and_false() -> None:
    tenant_id = uuid4()
    db = _session()
    db.execute(
        text(
            "INSERT INTO mod_files.stored_files "
            "(tenant_id, provider_code, storage_key, state, created_at) "
            "VALUES (:tenant, 'erp_s3', :key, 'available', :created_at)"
        ),
        {
            "tenant": tenant_id.hex,
            "key": "tenants/x/files/referenced",
            "created_at": "2026-09-20 00:00:00.000000",
        },
    )

    assert (
        is_storage_key_referenced(
            db, tenant_id=tenant_id, storage_key="tenants/x/files/referenced"
        )
        is True
    )
    assert (
        is_storage_key_referenced(
            db, tenant_id=tenant_id, storage_key="tenants/x/files/other"
        )
        is False
    )


def test_cleanup_partial_failure_survives_an_args_only_reconstruction() -> None:
    """Celery's result backend reconstructs an exception as ``cls(*exc.args)``
    on a pickle/JSON round trip -- this must not raise TypeError, and the
    summary must survive intact."""
    summary: dict[str, object] = {"deleted": 1, "failed_count": 1}
    original = CleanupPartialFailure("stopped after a failure", summary)

    rebuilt = CleanupPartialFailure(*original.args)

    assert rebuilt.summary == summary
    assert rebuilt.args == original.args


def test_cleanup_partial_failure_summary_defaults_to_none() -> None:
    exc = CleanupPartialFailure("stopped after a failure")
    assert exc.summary is None
