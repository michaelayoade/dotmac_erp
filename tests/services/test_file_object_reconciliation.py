"""Behavior and scope canaries for managed files object reports."""

from __future__ import annotations

import importlib
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from dotmac_files import ObjectInfo, StorageBoundaryViolation, list_objects
from dotmac_kernel.cache import PlatformScope, TenantScope
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.services.file_object_reconciliation import (
    _managed_scope_prefix,
    report_file_objects,
)


class ListingProvider:
    code = "erp_s3"

    def __init__(self, objects: tuple[ObjectInfo, ...]) -> None:
        self.objects = objects
        self.prefixes: list[str] = []
        self.delete_calls: list[str] = []

    def list(self, prefix: str):
        self.prefixes.append(prefix)
        return (item for item in self.objects if item.key.startswith(prefix))

    def delete(self, key: str) -> None:
        self.delete_calls.append(key)
        raise AssertionError("dry-run must never delete")


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
    connection.execute(
        text(
            "CREATE TABLE mod_files.platform_stored_files ("
            "provider_code VARCHAR(32), storage_key VARCHAR(500), "
            "state VARCHAR(32), created_at DATETIME)"
        )
    )
    return Session(bind=connection)


def test_tenant_report_separates_references_orphans_grace_and_missing() -> None:
    now = datetime(2026, 9, 27, tzinfo=UTC)
    tenant_id = uuid4()
    other_id = uuid4()
    prefix = f"tenants/{tenant_id}/files/"
    referenced = prefix + str(uuid4())
    old_orphan = prefix + str(uuid4())
    in_flight = prefix + str(uuid4())
    missing = prefix + str(uuid4())
    recent_missing = prefix + str(uuid4())
    provider = ListingProvider(
        (
            ObjectInfo(referenced, 10, now - timedelta(days=3)),
            ObjectInfo(old_orphan, 11, now - timedelta(days=3)),
            ObjectInfo(in_flight, 12, now - timedelta(minutes=5)),
            ObjectInfo(
                f"tenants/{other_id}/files/{uuid4()}", 13, now - timedelta(days=3)
            ),
        )
    )
    scope = TenantScope(tenant_id)
    observations = list_objects(provider, scope=scope)
    db = _session()
    db.execute(
        text(
            "INSERT INTO mod_files.stored_files "
            "(tenant_id, provider_code, storage_key, state, created_at) "
            "VALUES (:tenant, 'erp_s3', :key, 'available', :created_at)"
        ),
        [
            {
                "tenant": tenant_id.hex,
                "key": referenced,
                "created_at": "2026-09-20 00:00:00.000000",
            },
            {
                "tenant": tenant_id.hex,
                "key": missing,
                "created_at": "2026-09-20 00:00:00.000000",
            },
            {
                "tenant": tenant_id.hex,
                "key": recent_missing,
                "created_at": "2026-09-27 00:00:00.000000",
            },
            {
                "tenant": other_id.hex,
                "key": old_orphan,
                "created_at": "2026-09-20 00:00:00.000000",
            },
        ],
    )

    report = report_file_objects(
        db,
        scope=scope,
        provider_code=provider.code,
        observations=observations,
        observed_at=now,
    )

    assert provider.prefixes == [prefix]
    assert {item.key for item in report.objects} == {referenced, old_orphan, in_flight}
    assert report.candidate_keys == (old_orphan,)
    assert report.safe_summary()["in_flight_unreferenced"] == 1
    assert report.safe_summary()["missing_references"] == 1
    assert report.safe_summary()["boundary_drift"] == 0
    assert old_orphan not in str(report.safe_summary())
    assert provider.delete_calls == []


def test_platform_report_uses_only_platform_rows_and_prefix() -> None:
    now = datetime(2026, 9, 27, tzinfo=UTC)
    key = f"platform/files/{uuid4()}"
    provider = ListingProvider((ObjectInfo(key, 2, now - timedelta(days=2)),))
    scope = PlatformScope()
    observations = list_objects(provider, scope=scope)
    db = _session()
    db.execute(
        text(
            "INSERT INTO mod_files.stored_files "
            "(tenant_id, provider_code, storage_key, state, created_at) "
            "VALUES (:tenant, 'erp_s3', :key, 'available', :created_at)"
        ),
        {"tenant": uuid4().hex, "key": key, "created_at": "2026-09-20 00:00:00.000000"},
    )

    report = report_file_objects(
        db,
        scope=scope,
        provider_code=provider.code,
        observations=observations,
        observed_at=now,
    )

    assert provider.prefixes == ["platform/files/"]
    assert report.candidate_keys == (key,)
    assert report.safe_summary()["scope"] == "platform"
    assert provider.delete_calls == []


def test_report_refuses_provider_leak_across_scope() -> None:
    now = datetime(2026, 9, 27, tzinfo=UTC)
    provider = ListingProvider(())
    provider.list = lambda _prefix: (ObjectInfo("legacy/file", 1, now),)  # type: ignore[method-assign]
    with pytest.raises(StorageBoundaryViolation):
        list_objects(provider, scope=PlatformScope())
    assert provider.delete_calls == []


def test_out_of_scope_metadata_key_is_reported_as_reference_drift() -> None:
    now = datetime(2026, 9, 27, tzinfo=UTC)
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
            "key": f"legacy/{uuid4()}",
            "created_at": "2026-09-20 00:00:00.000000",
        },
    )
    report = report_file_objects(
        db,
        scope=TenantScope(tenant_id),
        provider_code="erp_s3",
        observations=(),
        observed_at=now,
    )
    assert report.candidate_keys == ()
    assert report.missing_references == ()
    assert len(report.boundary_drift) == 1
    assert report.safe_summary()["boundary_drift"] == 1


@pytest.mark.parametrize("scope", [TenantScope(uuid4()), PlatformScope()])
def test_erp_prefix_matches_the_pinned_public_files_listing(scope) -> None:
    """Mutation of either ERP prefix must disagree with a4's listing request."""
    provider = ListingProvider(())
    assert list_objects(provider, scope=scope) == ()
    assert provider.prefixes == [_managed_scope_prefix(scope)]


def test_task_lists_before_opening_the_tenant_session(monkeypatch) -> None:
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    events: list[str] = []
    provider = ListingProvider(())

    def observe(_provider, *, scope):
        events.append("list")
        return ()

    @contextmanager
    def scoped_session(_organization_id):
        events.append("session_open")
        yield object()
        events.append("session_closed")

    def compare(_db, **_kwargs):
        events.append("compare")
        return SimpleNamespace(safe_summary=lambda: {"dry_run": True})

    monkeypatch.setattr(task_module, "get_dotmac_files_read_provider", lambda: provider)
    monkeypatch.setattr(task_module, "list_objects", observe)
    monkeypatch.setattr(task_module, "session_for_org", scoped_session)
    monkeypatch.setattr(task_module, "report_file_objects", compare)

    assert task_module.report_tenant_file_objects.run(str(uuid4())) == {"dry_run": True}
    assert events == ["list", "session_open", "compare", "session_closed"]


def test_clean_task_dry_run_never_calls_the_deletion_seam(monkeypatch) -> None:
    """``report_file_objects`` always queries the real stored-file table
    (via ``dotmac_files.find_orphan_keys``) regardless of what was observed,
    so the fake session here must be a real, schema-bearing SQLite session
    (``_session()``), not a bare stand-in object -- a bare ``object()``
    cannot answer ``db.scalars(...)``."""
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    provider = ListingProvider(())
    delete_calls: list[tuple[object, str, str]] = []
    db = _session()

    @contextmanager
    def scoped_session(_organization_id):
        yield db

    def fake_delete(*, scope, key, expected_provider_code):
        delete_calls.append((scope, key, expected_provider_code))
        raise AssertionError("dry-run must never delete")

    monkeypatch.setattr(task_module, "get_dotmac_files_read_provider", lambda: provider)
    monkeypatch.setattr(task_module, "list_objects", lambda _p, *, scope: ())
    monkeypatch.setattr(task_module, "session_for_org", scoped_session)
    monkeypatch.setattr(task_module, "delete_reviewed_file_orphan", fake_delete)

    summary = task_module.clean_tenant_file_objects.run(str(uuid4()))

    assert summary["dry_run"] is True
    assert delete_calls == []


def test_clean_task_apply_requires_expected_plan_digest() -> None:
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    with pytest.raises(ValueError):
        task_module.clean_tenant_file_objects.run(
            str(uuid4()),
            apply=True,
            reviewed_older_than="2026-09-24T00:00:00+00:00",
            reviewed_plan_observed_at="2026-10-01T00:00:00+00:00",
        )


def test_clean_task_apply_requires_reviewed_older_than() -> None:
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    with pytest.raises(ValueError):
        task_module.clean_tenant_file_objects.run(
            str(uuid4()),
            apply=True,
            expected_plan_digest="x",
            reviewed_plan_observed_at="2026-10-01T00:00:00+00:00",
        )


def test_clean_task_apply_requires_reviewed_plan_observed_at() -> None:
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    with pytest.raises(ValueError):
        task_module.clean_tenant_file_objects.run(
            str(uuid4()),
            apply=True,
            expected_plan_digest="x",
            reviewed_older_than="2026-09-24T00:00:00+00:00",
        )


def test_clean_task_apply_refuses_a_naive_reviewed_older_than() -> None:
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    with pytest.raises(ValueError):
        task_module.clean_tenant_file_objects.run(
            str(uuid4()),
            apply=True,
            expected_plan_digest="x",
            reviewed_older_than="2026-09-24T00:00:00",
            reviewed_plan_observed_at="2026-10-01T00:00:00+00:00",
        )


def test_clean_task_apply_refuses_a_naive_reviewed_plan_observed_at() -> None:
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    with pytest.raises(ValueError):
        task_module.clean_tenant_file_objects.run(
            str(uuid4()),
            apply=True,
            expected_plan_digest="x",
            reviewed_older_than="2026-09-24T00:00:00+00:00",
            reviewed_plan_observed_at="2026-10-01T00:00:00",
        )


def test_clean_task_apply_refuses_an_inconsistent_reviewed_pair() -> None:
    """reviewed_older_than must equal reviewed_plan_observed_at - 168h exactly."""
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    with pytest.raises(ValueError):
        task_module.clean_tenant_file_objects.run(
            str(uuid4()),
            apply=True,
            expected_plan_digest="x",
            reviewed_older_than="2026-09-24T00:00:00+00:00",
            # Off by one hour from older_than + 168h.
            reviewed_plan_observed_at="2026-10-01T01:00:00+00:00",
        )


def _wire_scope(monkeypatch, task_module, provider, scope) -> None:
    monkeypatch.setattr(task_module, "get_dotmac_files_read_provider", lambda: provider)
    monkeypatch.setattr(
        task_module.OrganizationTenantContext,
        "for_organization",
        classmethod(lambda cls, org_id: SimpleNamespace(tenant_scope=scope)),
    )


class _FixedClock(datetime):
    """A ``datetime`` subclass whose ``now()`` returns a scripted sequence."""

    _times: list[datetime] = []

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls._times.pop(0)


def test_clean_task_apply_refuses_a_self_consistent_but_too_recent_cutoff() -> None:
    """Retention is now enforced STRUCTURALLY: apply's rebuild always uses a
    FIXED 168h grace against reviewed_plan_observed_at, so older_than can
    never legitimately be less than 168h before reviewed_plan_observed_at.
    A pair claiming otherwise is simply inconsistent (caught above) --
    there is no separate "too recent" runtime check left to exercise
    because the fixed grace makes a shorter retention unconstructible.
    """
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    reviewed_plan_observed_at = "2026-09-27T00:00:00+00:00"
    # A cutoff only 1 hour before "observed_at" is inconsistent with the
    # fixed 168h grace, so this is refused as an inconsistent pair, not a
    # separate "too recent" check.
    reviewed_older_than = "2026-09-26T23:00:00+00:00"

    with pytest.raises(ValueError):
        task_module.clean_tenant_file_objects.run(
            str(uuid4()),
            apply=True,
            expected_plan_digest="x",
            reviewed_older_than=reviewed_older_than,
            reviewed_plan_observed_at=reviewed_plan_observed_at,
        )


class _RecheckProvider:
    """A provider whose per-key recheck answer differs from its full listing.

    ``list()`` is called two ways by the task: with the SCOPE prefix
    (ends with ``/``) for the initial full listing, and with an exact KEY
    (the recheck immediately before delete). This double lets tests control
    each independently -- e.g. an object present at listing time but gone,
    or freshened, by the time of the per-object recheck.
    """

    code = "erp_s3"

    def __init__(
        self,
        initial: tuple[ObjectInfo, ...],
        recheck_by_key: dict[str, ObjectInfo | None] | None = None,
    ) -> None:
        self.initial = initial
        self.recheck_by_key = recheck_by_key or {}

    def list(self, prefix: str):
        if prefix.endswith("/"):
            return (item for item in self.initial if item.key.startswith(prefix))
        if prefix in self.recheck_by_key:
            value = self.recheck_by_key[prefix]
            return (value,) if value is not None else ()
        return (item for item in self.initial if item.key == prefix)


def _fake_record_cleanup_run_started(_db, **_kwargs):
    """Slice 2b's real recording functions assume the durable
    ``file_orphan_cleanup_runs``/``file_orphan_cleanup_deletions`` schema
    (see ``tests/services/test_file_orphan_cleanup_records.py``), which this
    module's bespoke ``_session()`` fixture does not create -- these tests
    are about the plan/authorize/recheck behavior, not persistence. Faked
    here so the full apply round trip still exercises everything up to and
    including recording's CALL SITES in the task, without a real schema.
    """
    return uuid4()


def _fake_record_cleanup_keys_planned(_db, **_kwargs) -> None:
    return None


def _fake_record_cleanup_key_outcome(_db, **_kwargs) -> None:
    return None


def _fake_record_cleanup_run_finished(_db, **_kwargs) -> None:
    return None


def _run_dry_run_then_apply(
    monkeypatch,
    task_module,
    *,
    tenant_id,
    scope,
    provider,
    db,
    dry_run_time: datetime,
    apply_time: datetime,
    max_deletions: int = 100,
    apply_org_id=None,
):
    @contextmanager
    def scoped_session(_organization_id):
        yield db

    monkeypatch.setattr(task_module, "session_for_org", scoped_session)
    monkeypatch.setattr(
        task_module, "record_cleanup_run_started", _fake_record_cleanup_run_started
    )
    monkeypatch.setattr(
        task_module, "record_cleanup_keys_planned", _fake_record_cleanup_keys_planned
    )
    monkeypatch.setattr(
        task_module, "record_cleanup_key_outcome", _fake_record_cleanup_key_outcome
    )
    monkeypatch.setattr(
        task_module, "record_cleanup_run_finished", _fake_record_cleanup_run_finished
    )
    _wire_scope(monkeypatch, task_module, provider, scope)

    _FixedClock._times = [dry_run_time]
    monkeypatch.setattr(task_module, "datetime", _FixedClock)
    dry_run_summary = task_module.clean_tenant_file_objects.run(
        str(tenant_id), max_deletions=max_deletions
    )

    _FixedClock._times = [apply_time]
    apply_id = apply_org_id if apply_org_id is not None else tenant_id
    return dry_run_summary, apply_id


def _seed_healthy_reference(
    provider: _RecheckProvider, db: Session, *, tenant_id, prefix: str, moment: datetime
) -> None:
    """Add one OLD, referenced, available object so the reference view looks
    healthy under the NEW old-object-only safety check.

    ``authorize_apply`` now refuses on ``old_managed_objects > 0 and
    old_referenced_objects == 0`` -- so the seeded reference must itself be
    OLDER than the reviewed cutoff (well past the 168h retention window), or
    it would not count towards ``old_referenced_objects`` at all and this
    fixture would trip the very check it exists to satisfy.
    """
    healthy_key = prefix + str(uuid4())
    old_enough_moment = moment - timedelta(days=30)
    provider.initial = (
        *provider.initial,
        ObjectInfo(healthy_key, 1, old_enough_moment),
    )
    db.execute(
        text(
            "INSERT INTO mod_files.stored_files "
            "(tenant_id, provider_code, storage_key, state, created_at) "
            "VALUES (:tenant, 'erp_s3', :key, 'available', :created_at)"
        ),
        {
            "tenant": tenant_id.hex,
            "key": healthy_key,
            "created_at": old_enough_moment.strftime("%Y-%m-%d %H:%M:%S.%f"),
        },
    )


def test_clean_task_apply_round_trip_succeeds_when_the_listing_is_unchanged(
    monkeypatch,
) -> None:
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    dry_run_time = datetime(2026, 9, 24, tzinfo=UTC)
    apply_time = dry_run_time + timedelta(hours=2)
    tenant_id = uuid4()
    prefix = f"tenants/{tenant_id}/files/"
    old_orphan = prefix + str(uuid4())
    scope = TenantScope(tenant_id)
    provider = _RecheckProvider(
        (ObjectInfo(old_orphan, 11, dry_run_time - timedelta(days=10)),)
    )
    db = _session()
    _seed_healthy_reference(
        provider, db, tenant_id=tenant_id, prefix=prefix, moment=dry_run_time
    )

    delete_calls: list[tuple[object, str, str]] = []

    def fake_delete(*, scope, key, expected_provider_code):
        delete_calls.append((scope, key, expected_provider_code))

    monkeypatch.setattr(task_module, "delete_reviewed_file_orphan", fake_delete)

    dry_run_summary, apply_id = _run_dry_run_then_apply(
        monkeypatch,
        task_module,
        tenant_id=tenant_id,
        scope=scope,
        provider=provider,
        db=db,
        dry_run_time=dry_run_time,
        apply_time=apply_time,
    )
    digest = dry_run_summary["plan_digest"]
    older_than = dry_run_summary["older_than"]

    summary = task_module.clean_tenant_file_objects.run(
        str(apply_id),
        apply=True,
        actor="operator@example.com",
        expected_plan_digest=digest,
        reviewed_older_than=older_than,
        reviewed_plan_observed_at=dry_run_summary["plan_observed_at"],
    )

    assert delete_calls == [(scope, old_orphan, "erp_s3")]
    assert summary["dry_run"] is False
    assert summary["deleted"] == 1
    assert summary["deleted_count"] == 1
    assert summary["rechecked_referenced_count"] == 0


def test_clean_task_apply_succeeds_through_the_real_report_despite_a_fresh_upload(
    monkeypatch,
) -> None:
    """End-to-end (through the real ``report_file_objects``, not a fake):
    a brand-new object plus a matching new row appear between the dry-run
    and the apply. The new object is newer than ``plan_observed_at``, so it
    is neither old nor a candidate, and the apply still succeeds and deletes
    only the original orphan.
    """
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    dry_run_time = datetime(2026, 9, 24, tzinfo=UTC)
    apply_time = dry_run_time + timedelta(hours=2)
    tenant_id = uuid4()
    prefix = f"tenants/{tenant_id}/files/"
    old_orphan = prefix + str(uuid4())
    scope = TenantScope(tenant_id)
    provider = _RecheckProvider(
        (ObjectInfo(old_orphan, 11, dry_run_time - timedelta(days=10)),)
    )
    db = _session()
    _seed_healthy_reference(
        provider, db, tenant_id=tenant_id, prefix=prefix, moment=dry_run_time
    )

    delete_calls: list[str] = []
    monkeypatch.setattr(
        task_module,
        "delete_reviewed_file_orphan",
        lambda **kw: delete_calls.append(kw["key"]),
    )

    dry_run_summary, apply_id = _run_dry_run_then_apply(
        monkeypatch,
        task_module,
        tenant_id=tenant_id,
        scope=scope,
        provider=provider,
        db=db,
        dry_run_time=dry_run_time,
        apply_time=apply_time,
    )
    plan_observed_at = datetime.fromisoformat(dry_run_summary["plan_observed_at"])

    # A brand-new upload -- newer than plan_observed_at -- plus a matching
    # new row, both appearing after the dry-run was taken.
    new_key = prefix + str(uuid4())
    new_moment = plan_observed_at + timedelta(hours=1)
    provider.initial = (*provider.initial, ObjectInfo(new_key, 5, new_moment))
    db.execute(
        text(
            "INSERT INTO mod_files.stored_files "
            "(tenant_id, provider_code, storage_key, state, created_at) "
            "VALUES (:tenant, 'erp_s3', :key, 'available', :created_at)"
        ),
        {
            "tenant": tenant_id.hex,
            "key": new_key,
            "created_at": new_moment.strftime("%Y-%m-%d %H:%M:%S.%f"),
        },
    )

    summary = task_module.clean_tenant_file_objects.run(
        str(apply_id),
        apply=True,
        actor="operator@example.com",
        expected_plan_digest=dry_run_summary["plan_digest"],
        reviewed_older_than=dry_run_summary["older_than"],
        reviewed_plan_observed_at=dry_run_summary["plan_observed_at"],
    )

    assert delete_calls == [old_orphan]
    assert summary["dry_run"] is False
    assert summary["deleted"] == 1
    # The new object is neither old nor a candidate.
    assert summary["old_managed_objects"] == dry_run_summary["old_managed_objects"]
    assert summary["orphan_candidates"] == dry_run_summary["orphan_candidates"]
    assert new_key not in delete_calls


def test_clean_task_apply_refuses_when_the_listing_changed_since_review(
    monkeypatch,
) -> None:
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    dry_run_time = datetime(2026, 9, 24, tzinfo=UTC)
    apply_time = dry_run_time + timedelta(hours=2)
    tenant_id = uuid4()
    prefix = f"tenants/{tenant_id}/files/"
    old_orphan = prefix + str(uuid4())
    new_orphan = prefix + str(uuid4())
    scope = TenantScope(tenant_id)
    provider = _RecheckProvider(
        (ObjectInfo(old_orphan, 11, dry_run_time - timedelta(days=10)),)
    )
    db = _session()

    dry_run_summary, apply_id = _run_dry_run_then_apply(
        monkeypatch,
        task_module,
        tenant_id=tenant_id,
        scope=scope,
        provider=provider,
        db=db,
        dry_run_time=dry_run_time,
        apply_time=apply_time,
    )
    digest = dry_run_summary["plan_digest"]
    older_than = dry_run_summary["older_than"]

    provider.initial = (
        *provider.initial,
        ObjectInfo(new_orphan, 12, dry_run_time - timedelta(days=10)),
    )

    from app.services.file_object_cleanup import CleanupPlanDrift

    with pytest.raises(CleanupPlanDrift):
        task_module.clean_tenant_file_objects.run(
            str(apply_id),
            apply=True,
            actor="operator@example.com",
            expected_plan_digest=digest,
            reviewed_older_than=older_than,
            reviewed_plan_observed_at=dry_run_summary["plan_observed_at"],
        )


def test_clean_task_apply_refuses_an_expired_plan(monkeypatch) -> None:
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    dry_run_time = datetime(2026, 9, 24, tzinfo=UTC)
    apply_time = dry_run_time + timedelta(hours=200)  # far past the 24h window
    tenant_id = uuid4()
    prefix = f"tenants/{tenant_id}/files/"
    old_orphan = prefix + str(uuid4())
    scope = TenantScope(tenant_id)
    provider = _RecheckProvider(
        (ObjectInfo(old_orphan, 11, dry_run_time - timedelta(days=10)),)
    )
    db = _session()

    dry_run_summary, apply_id = _run_dry_run_then_apply(
        monkeypatch,
        task_module,
        tenant_id=tenant_id,
        scope=scope,
        provider=provider,
        db=db,
        dry_run_time=dry_run_time,
        apply_time=apply_time,
    )

    from app.services.file_object_cleanup import CleanupPlanExpired

    with pytest.raises(CleanupPlanExpired):
        task_module.clean_tenant_file_objects.run(
            str(apply_id),
            apply=True,
            actor="operator@example.com",
            expected_plan_digest=dry_run_summary["plan_digest"],
            reviewed_older_than=dry_run_summary["older_than"],
            reviewed_plan_observed_at=dry_run_summary["plan_observed_at"],
        )


def test_clean_task_apply_refuses_a_digest_reused_against_a_different_org(
    monkeypatch,
) -> None:
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    dry_run_time = datetime(2026, 9, 24, tzinfo=UTC)
    apply_time = dry_run_time + timedelta(hours=2)
    tenant_id = uuid4()
    other_tenant_id = uuid4()
    prefix = f"tenants/{tenant_id}/files/"
    old_orphan = prefix + str(uuid4())
    scope = TenantScope(tenant_id)
    provider = _RecheckProvider(
        (ObjectInfo(old_orphan, 11, dry_run_time - timedelta(days=10)),)
    )
    db = _session()

    dry_run_summary, _ = _run_dry_run_then_apply(
        monkeypatch,
        task_module,
        tenant_id=tenant_id,
        scope=scope,
        provider=provider,
        db=db,
        dry_run_time=dry_run_time,
        apply_time=apply_time,
    )

    # Re-wire the scope lookup to a DIFFERENT tenant for the apply call --
    # the fresh plan's tenant_id changes, so its digest can never match.
    other_scope = TenantScope(other_tenant_id)
    monkeypatch.setattr(
        task_module.OrganizationTenantContext,
        "for_organization",
        classmethod(lambda cls, org_id: SimpleNamespace(tenant_scope=other_scope)),
    )

    from app.services.file_object_cleanup import CleanupPlanDrift

    with pytest.raises(CleanupPlanDrift):
        task_module.clean_tenant_file_objects.run(
            str(other_tenant_id),
            apply=True,
            actor="operator@example.com",
            expected_plan_digest=dry_run_summary["plan_digest"],
            reviewed_older_than=dry_run_summary["older_than"],
            reviewed_plan_observed_at=dry_run_summary["plan_observed_at"],
        )


def test_clean_task_apply_refuses_candidates_over_the_cap(monkeypatch) -> None:
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    dry_run_time = datetime(2026, 9, 24, tzinfo=UTC)
    apply_time = dry_run_time + timedelta(hours=2)
    tenant_id = uuid4()
    prefix = f"tenants/{tenant_id}/files/"
    orphans = tuple(prefix + str(uuid4()) for _ in range(3))
    scope = TenantScope(tenant_id)
    provider = _RecheckProvider(
        tuple(ObjectInfo(key, 11, dry_run_time - timedelta(days=10)) for key in orphans)
    )
    db = _session()

    dry_run_summary, apply_id = _run_dry_run_then_apply(
        monkeypatch,
        task_module,
        tenant_id=tenant_id,
        scope=scope,
        provider=provider,
        db=db,
        dry_run_time=dry_run_time,
        apply_time=apply_time,
        max_deletions=2,
    )

    from app.services.file_object_cleanup import CleanupCapExceeded

    with pytest.raises(CleanupCapExceeded):
        task_module.clean_tenant_file_objects.run(
            str(apply_id),
            apply=True,
            max_deletions=2,
            actor="operator@example.com",
            expected_plan_digest=dry_run_summary["plan_digest"],
            reviewed_older_than=dry_run_summary["older_than"],
            reviewed_plan_observed_at=dry_run_summary["plan_observed_at"],
        )


def test_clean_task_apply_accepts_a_non_utc_offset_reviewed_older_than(
    monkeypatch,
) -> None:
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    dry_run_time = datetime(2026, 9, 24, tzinfo=UTC)
    apply_time = dry_run_time + timedelta(hours=2)
    tenant_id = uuid4()
    prefix = f"tenants/{tenant_id}/files/"
    old_orphan = prefix + str(uuid4())
    scope = TenantScope(tenant_id)
    provider = _RecheckProvider(
        (ObjectInfo(old_orphan, 11, dry_run_time - timedelta(days=10)),)
    )
    db = _session()
    _seed_healthy_reference(
        provider, db, tenant_id=tenant_id, prefix=prefix, moment=dry_run_time
    )

    monkeypatch.setattr(task_module, "delete_reviewed_file_orphan", lambda **_kw: None)

    dry_run_summary, apply_id = _run_dry_run_then_apply(
        monkeypatch,
        task_module,
        tenant_id=tenant_id,
        scope=scope,
        provider=provider,
        db=db,
        dry_run_time=dry_run_time,
        apply_time=apply_time,
    )

    # Re-express the exact same instant with a non-UTC offset string.
    from datetime import timezone as _tz

    older_than_value = datetime.fromisoformat(dry_run_summary["older_than"])
    equivalent = older_than_value.astimezone(_tz(timedelta(hours=5))).isoformat()

    summary = task_module.clean_tenant_file_objects.run(
        str(apply_id),
        apply=True,
        actor="operator@example.com",
        expected_plan_digest=dry_run_summary["plan_digest"],
        reviewed_older_than=equivalent,
        reviewed_plan_observed_at=dry_run_summary["plan_observed_at"],
    )
    assert summary["dry_run"] is False
    assert summary["deleted"] == 1


def test_clean_task_recheck_skips_a_key_that_gained_a_reference_mid_loop(
    monkeypatch,
) -> None:
    """A key referenced only AFTER the fresh plan snapshot is not deleted.

    A row appearing before the fresh report is built already shows up as
    digest drift (``CleanupPlanDrift``) via the plan rebuild itself -- that
    is not what per-object recheck is for. The recheck instead guards a
    narrower race: something claims a key WHILE the recheck-and-delete loop
    is already running (i.e. between the fresh snapshot and this specific
    key's own turn). Simulated here by having key A's delete succeed with a
    side effect that inserts a reference row for key B, then asserting key
    B's independent, freshly-opened recheck sees it and skips.
    """
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    dry_run_time = datetime(2026, 9, 24, tzinfo=UTC)
    apply_time = dry_run_time + timedelta(hours=2)
    tenant_id = uuid4()
    prefix = f"tenants/{tenant_id}/files/"
    key_a, key_b = sorted((prefix + str(uuid4()), prefix + str(uuid4())))
    scope = TenantScope(tenant_id)
    provider = _RecheckProvider(
        (
            ObjectInfo(key_a, 11, dry_run_time - timedelta(days=10)),
            ObjectInfo(key_b, 11, dry_run_time - timedelta(days=10)),
        )
    )
    db = _session()
    _seed_healthy_reference(
        provider, db, tenant_id=tenant_id, prefix=prefix, moment=dry_run_time
    )

    delete_calls: list[str] = []

    def fake_delete(*, scope, key, expected_provider_code):
        delete_calls.append(key)
        if key == key_a:
            # A concurrent claim on key_b appears while key_a is deleting.
            db.execute(
                text(
                    "INSERT INTO mod_files.stored_files "
                    "(tenant_id, provider_code, storage_key, state, created_at) "
                    "VALUES (:tenant, 'erp_s3', :key, 'available', :created_at)"
                ),
                {
                    "tenant": tenant_id.hex,
                    "key": key_b,
                    "created_at": "2026-09-24 00:00:00.000000",
                },
            )

    monkeypatch.setattr(task_module, "delete_reviewed_file_orphan", fake_delete)

    dry_run_summary, apply_id = _run_dry_run_then_apply(
        monkeypatch,
        task_module,
        tenant_id=tenant_id,
        scope=scope,
        provider=provider,
        db=db,
        dry_run_time=dry_run_time,
        apply_time=apply_time,
    )

    summary = task_module.clean_tenant_file_objects.run(
        str(apply_id),
        apply=True,
        actor="operator@example.com",
        expected_plan_digest=dry_run_summary["plan_digest"],
        reviewed_older_than=dry_run_summary["older_than"],
        reviewed_plan_observed_at=dry_run_summary["plan_observed_at"],
    )

    assert delete_calls == [key_a]
    assert summary["deleted"] == 1
    assert summary["rechecked_referenced_count"] == 1


def test_clean_task_recheck_reports_missing_and_too_new_without_deleting(
    monkeypatch,
) -> None:
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    dry_run_time = datetime(2026, 9, 24, tzinfo=UTC)
    apply_time = dry_run_time + timedelta(hours=2)
    tenant_id = uuid4()
    prefix = f"tenants/{tenant_id}/files/"
    vanished = prefix + str(uuid4())
    freshened = prefix + str(uuid4())
    scope = TenantScope(tenant_id)
    provider = _RecheckProvider(
        (
            ObjectInfo(vanished, 11, dry_run_time - timedelta(days=10)),
            ObjectInfo(freshened, 12, dry_run_time - timedelta(days=10)),
        ),
        recheck_by_key={
            vanished: None,
            freshened: ObjectInfo(freshened, 12, apply_time - timedelta(minutes=1)),
        },
    )
    db = _session()
    _seed_healthy_reference(
        provider, db, tenant_id=tenant_id, prefix=prefix, moment=dry_run_time
    )

    delete_calls: list[str] = []
    monkeypatch.setattr(
        task_module,
        "delete_reviewed_file_orphan",
        lambda **kw: delete_calls.append(kw["key"]),
    )

    dry_run_summary, apply_id = _run_dry_run_then_apply(
        monkeypatch,
        task_module,
        tenant_id=tenant_id,
        scope=scope,
        provider=provider,
        db=db,
        dry_run_time=dry_run_time,
        apply_time=apply_time,
    )

    summary = task_module.clean_tenant_file_objects.run(
        str(apply_id),
        apply=True,
        actor="operator@example.com",
        expected_plan_digest=dry_run_summary["plan_digest"],
        reviewed_older_than=dry_run_summary["older_than"],
        reviewed_plan_observed_at=dry_run_summary["plan_observed_at"],
    )

    assert delete_calls == []
    assert summary["deleted"] == 0
    assert summary["already_absent_count"] == 1
    assert summary["rechecked_too_new_count"] == 1


def test_clean_task_recheck_stops_after_the_first_delete_failure(monkeypatch) -> None:
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    dry_run_time = datetime(2026, 9, 24, tzinfo=UTC)
    apply_time = dry_run_time + timedelta(hours=2)
    tenant_id = uuid4()
    prefix = f"tenants/{tenant_id}/files/"
    first_key, second_key = sorted((prefix + str(uuid4()), prefix + str(uuid4())))
    scope = TenantScope(tenant_id)
    provider = _RecheckProvider(
        (
            ObjectInfo(first_key, 11, dry_run_time - timedelta(days=10)),
            ObjectInfo(second_key, 11, dry_run_time - timedelta(days=10)),
        )
    )
    db = _session()
    _seed_healthy_reference(
        provider, db, tenant_id=tenant_id, prefix=prefix, moment=dry_run_time
    )

    delete_calls: list[str] = []

    def fake_delete(*, scope, key, expected_provider_code):
        delete_calls.append(key)
        if key == second_key:
            raise RuntimeError("boom")

    monkeypatch.setattr(task_module, "delete_reviewed_file_orphan", fake_delete)

    dry_run_summary, apply_id = _run_dry_run_then_apply(
        monkeypatch,
        task_module,
        tenant_id=tenant_id,
        scope=scope,
        provider=provider,
        db=db,
        dry_run_time=dry_run_time,
        apply_time=apply_time,
    )

    from app.services.file_object_cleanup import CleanupPartialFailure

    with pytest.raises(CleanupPartialFailure) as raised:
        task_module.clean_tenant_file_objects.run(
            str(apply_id),
            apply=True,
            actor="operator@example.com",
            expected_plan_digest=dry_run_summary["plan_digest"],
            reviewed_older_than=dry_run_summary["older_than"],
            reviewed_plan_observed_at=dry_run_summary["plan_observed_at"],
        )

    assert delete_calls == [first_key, second_key]
    summary = raised.value.summary
    assert summary is not None
    assert summary["deleted"] == 1
    assert summary["failed_count"] == 1
    assert summary["failure_exception_types"] == ["RuntimeError"]


def test_clean_task_recheck_records_a_reference_check_failure_as_failed(
    monkeypatch,
) -> None:
    """Any exception anywhere in a key's body -- not just the delete call
    itself -- records that key as failed and stops the loop."""
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    dry_run_time = datetime(2026, 9, 24, tzinfo=UTC)
    apply_time = dry_run_time + timedelta(hours=2)
    tenant_id = uuid4()
    prefix = f"tenants/{tenant_id}/files/"
    first_key, second_key = sorted((prefix + str(uuid4()), prefix + str(uuid4())))
    scope = TenantScope(tenant_id)
    provider = _RecheckProvider(
        (
            ObjectInfo(first_key, 11, dry_run_time - timedelta(days=10)),
            ObjectInfo(second_key, 11, dry_run_time - timedelta(days=10)),
        )
    )
    db = _session()
    _seed_healthy_reference(
        provider, db, tenant_id=tenant_id, prefix=prefix, moment=dry_run_time
    )

    delete_calls: list[str] = []

    def fake_delete(*, scope, key, expected_provider_code):
        delete_calls.append(key)

    def fake_is_referenced(db, *, tenant_id, storage_key):
        if storage_key == second_key:
            raise RuntimeError("db unavailable")
        return False

    monkeypatch.setattr(task_module, "delete_reviewed_file_orphan", fake_delete)
    monkeypatch.setattr(task_module, "is_storage_key_referenced", fake_is_referenced)

    dry_run_summary, apply_id = _run_dry_run_then_apply(
        monkeypatch,
        task_module,
        tenant_id=tenant_id,
        scope=scope,
        provider=provider,
        db=db,
        dry_run_time=dry_run_time,
        apply_time=apply_time,
    )

    from app.services.file_object_cleanup import CleanupPartialFailure

    with pytest.raises(CleanupPartialFailure) as raised:
        task_module.clean_tenant_file_objects.run(
            str(apply_id),
            apply=True,
            actor="operator@example.com",
            expected_plan_digest=dry_run_summary["plan_digest"],
            reviewed_older_than=dry_run_summary["older_than"],
            reviewed_plan_observed_at=dry_run_summary["plan_observed_at"],
        )

    assert delete_calls == [first_key]
    summary = raised.value.summary
    assert summary is not None
    assert summary["deleted"] == 1
    assert summary["failed_count"] == 1
    assert summary["failure_exception_types"] == ["RuntimeError"]


# --- Slice 2b: the durable run/outcome record (2026-09-28) ---


def test_clean_task_apply_without_an_actor_is_refused() -> None:
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    with pytest.raises(ValueError, match="actor"):
        task_module.clean_tenant_file_objects.run(
            str(uuid4()),
            apply=True,
            expected_plan_digest="x",
            reviewed_older_than="2026-09-24T00:00:00+00:00",
            reviewed_plan_observed_at="2026-10-01T00:00:00+00:00",
        )


def test_clean_task_apply_records_the_started_row_before_any_delete_and_outcomes_in_order(
    monkeypatch,
) -> None:
    """One shared events list orders every recording call and every delete
    call -- proving the "started" row is committed before the FIRST delete,
    and that each key's outcome is recorded (and committed) immediately, in
    plan order, one at a time."""
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    dry_run_time = datetime(2026, 9, 24, tzinfo=UTC)
    apply_time = dry_run_time + timedelta(hours=2)
    tenant_id = uuid4()
    prefix = f"tenants/{tenant_id}/files/"
    key_a, key_b = sorted((prefix + str(uuid4()), prefix + str(uuid4())))
    scope = TenantScope(tenant_id)
    provider = _RecheckProvider(
        (
            ObjectInfo(key_a, 11, dry_run_time - timedelta(days=10)),
            ObjectInfo(key_b, 11, dry_run_time - timedelta(days=10)),
        )
    )
    db = _session()
    _seed_healthy_reference(
        provider, db, tenant_id=tenant_id, prefix=prefix, moment=dry_run_time
    )

    events: list[str] = []
    run_id = uuid4()

    def fake_delete(*, scope, key, expected_provider_code):
        events.append(f"delete:{key}")

    def fake_started(_db, *, tenant_id, plan, actor, invocation_id):
        events.append("started")
        return run_id

    def fake_planned(_db, *, tenant_id, run_id, keys):
        events.append("planned")

    def fake_outcome(
        _db,
        *,
        tenant_id,
        run_id,
        storage_key,
        outcome,
        error_class=None,
        observed_last_modified=None,
    ):
        events.append(f"outcome:{storage_key}:{outcome}")

    def fake_finished(_db, *, tenant_id, run_id, status, outcome_counts):
        events.append(f"finished:{status}")

    monkeypatch.setattr(task_module, "delete_reviewed_file_orphan", fake_delete)

    dry_run_summary, apply_id = _run_dry_run_then_apply(
        monkeypatch,
        task_module,
        tenant_id=tenant_id,
        scope=scope,
        provider=provider,
        db=db,
        dry_run_time=dry_run_time,
        apply_time=apply_time,
    )
    # Re-patch AFTER the dry run (which never touches recording) so these
    # event-tracking fakes -- not `_run_dry_run_then_apply`'s own no-op
    # fakes -- are the ones the apply call below actually exercises.
    monkeypatch.setattr(task_module, "record_cleanup_run_started", fake_started)
    monkeypatch.setattr(task_module, "record_cleanup_keys_planned", fake_planned)
    monkeypatch.setattr(task_module, "record_cleanup_key_outcome", fake_outcome)
    monkeypatch.setattr(task_module, "record_cleanup_run_finished", fake_finished)

    summary = task_module.clean_tenant_file_objects.run(
        str(apply_id),
        apply=True,
        actor="operator@example.com",
        expected_plan_digest=dry_run_summary["plan_digest"],
        reviewed_older_than=dry_run_summary["older_than"],
        reviewed_plan_observed_at=dry_run_summary["plan_observed_at"],
    )

    assert summary["deleted"] == 2
    assert events == [
        "started",
        "planned",
        f"delete:{key_a}",
        f"outcome:{key_a}:deleted",
        f"delete:{key_b}",
        f"outcome:{key_b}:deleted",
        "finished:completed",
    ]


def test_clean_task_apply_commits_started_and_planned_together_before_first_delete(
    monkeypatch,
) -> None:
    """A dedicated commit spy on the shared records session, proving the
    FIRST commit happens right after "started" and "planned" are both
    written, and strictly before the first delete is even attempted."""
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    dry_run_time = datetime(2026, 9, 24, tzinfo=UTC)
    apply_time = dry_run_time + timedelta(hours=2)
    tenant_id = uuid4()
    prefix = f"tenants/{tenant_id}/files/"
    key_a, key_b = sorted((prefix + str(uuid4()), prefix + str(uuid4())))
    scope = TenantScope(tenant_id)
    provider = _RecheckProvider(
        (
            ObjectInfo(key_a, 11, dry_run_time - timedelta(days=10)),
            ObjectInfo(key_b, 11, dry_run_time - timedelta(days=10)),
        )
    )
    db = _session()
    _seed_healthy_reference(
        provider, db, tenant_id=tenant_id, prefix=prefix, moment=dry_run_time
    )

    events: list[str] = []
    run_id = uuid4()

    def fake_delete(*, scope, key, expected_provider_code):
        events.append(f"delete:{key}")

    def fake_started(_db, *, tenant_id, plan, actor, invocation_id):
        events.append("started")
        return run_id

    def fake_planned(_db, *, tenant_id, run_id, keys):
        events.append("planned")

    def fake_outcome(_db, **_kwargs):
        events.append("outcome")

    def fake_finished(_db, **_kwargs):
        events.append("finished")

    monkeypatch.setattr(task_module, "delete_reviewed_file_orphan", fake_delete)

    dry_run_summary, apply_id = _run_dry_run_then_apply(
        monkeypatch,
        task_module,
        tenant_id=tenant_id,
        scope=scope,
        provider=provider,
        db=db,
        dry_run_time=dry_run_time,
        apply_time=apply_time,
    )
    monkeypatch.setattr(task_module, "record_cleanup_run_started", fake_started)
    monkeypatch.setattr(task_module, "record_cleanup_keys_planned", fake_planned)
    monkeypatch.setattr(task_module, "record_cleanup_key_outcome", fake_outcome)
    monkeypatch.setattr(task_module, "record_cleanup_run_finished", fake_finished)

    original_commit = db.commit

    def spy_commit():
        events.append("commit")
        return original_commit()

    monkeypatch.setattr(db, "commit", spy_commit)

    task_module.clean_tenant_file_objects.run(
        str(apply_id),
        apply=True,
        actor="operator@example.com",
        expected_plan_digest=dry_run_summary["plan_digest"],
        reviewed_older_than=dry_run_summary["older_than"],
        reviewed_plan_observed_at=dry_run_summary["plan_observed_at"],
    )

    # The FIRST commit follows "started" and "planned" directly, and comes
    # strictly before the first delete -- not just eventually, before it.
    assert events[:3] == ["started", "planned", "commit"]
    assert events.index("commit") < events.index(f"delete:{key_a}")


def test_clean_task_apply_records_partial_failure_and_stops_further_deletes(
    monkeypatch,
) -> None:
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    dry_run_time = datetime(2026, 9, 24, tzinfo=UTC)
    apply_time = dry_run_time + timedelta(hours=2)
    tenant_id = uuid4()
    prefix = f"tenants/{tenant_id}/files/"
    first_key, second_key = sorted((prefix + str(uuid4()), prefix + str(uuid4())))
    scope = TenantScope(tenant_id)
    provider = _RecheckProvider(
        (
            ObjectInfo(first_key, 11, dry_run_time - timedelta(days=10)),
            ObjectInfo(second_key, 11, dry_run_time - timedelta(days=10)),
        )
    )
    db = _session()
    _seed_healthy_reference(
        provider, db, tenant_id=tenant_id, prefix=prefix, moment=dry_run_time
    )

    events: list[str] = []

    def fake_delete(*, scope, key, expected_provider_code):
        events.append(f"delete:{key}")
        if key == first_key:
            raise RuntimeError("boom")

    def fake_outcome(
        _db,
        *,
        tenant_id,
        run_id,
        storage_key,
        outcome,
        error_class=None,
        observed_last_modified=None,
    ):
        events.append(f"outcome:{storage_key}:{outcome}:{error_class}")

    def fake_finished(_db, *, tenant_id, run_id, status, outcome_counts):
        events.append(f"finished:{status}")

    monkeypatch.setattr(task_module, "delete_reviewed_file_orphan", fake_delete)

    dry_run_summary, apply_id = _run_dry_run_then_apply(
        monkeypatch,
        task_module,
        tenant_id=tenant_id,
        scope=scope,
        provider=provider,
        db=db,
        dry_run_time=dry_run_time,
        apply_time=apply_time,
    )
    monkeypatch.setattr(
        task_module, "record_cleanup_run_started", _fake_record_cleanup_run_started
    )
    monkeypatch.setattr(
        task_module, "record_cleanup_keys_planned", _fake_record_cleanup_keys_planned
    )
    monkeypatch.setattr(task_module, "record_cleanup_key_outcome", fake_outcome)
    monkeypatch.setattr(task_module, "record_cleanup_run_finished", fake_finished)

    from app.services.file_object_cleanup import CleanupPartialFailure

    with pytest.raises(CleanupPartialFailure):
        task_module.clean_tenant_file_objects.run(
            str(apply_id),
            apply=True,
            actor="operator@example.com",
            expected_plan_digest=dry_run_summary["plan_digest"],
            reviewed_older_than=dry_run_summary["older_than"],
            reviewed_plan_observed_at=dry_run_summary["plan_observed_at"],
        )

    # Only the failing key was attempted -- the loop stops at the first
    # failure, so the second key is never even delete-attempted.
    assert events == [
        f"delete:{first_key}",
        f"outcome:{first_key}:failed:RuntimeError",
        "finished:partial_failure",
    ]


def test_clean_task_apply_stops_deleting_when_recording_itself_raises(
    monkeypatch,
) -> None:
    """If recording an outcome raises, the exception propagates immediately
    and no further key is ever delete-attempted -- a delete must never
    outrun its own durable record. Also proves the actual FIX this guards:
    the raw key is already durable (planned) before ANY delete runs, so even
    though the outcome write for key_a fails, key_a's raw storage key was
    never only-in-memory."""
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    dry_run_time = datetime(2026, 9, 24, tzinfo=UTC)
    apply_time = dry_run_time + timedelta(hours=2)
    tenant_id = uuid4()
    prefix = f"tenants/{tenant_id}/files/"
    key_a, key_b = sorted((prefix + str(uuid4()), prefix + str(uuid4())))
    scope = TenantScope(tenant_id)
    provider = _RecheckProvider(
        (
            ObjectInfo(key_a, 11, dry_run_time - timedelta(days=10)),
            ObjectInfo(key_b, 11, dry_run_time - timedelta(days=10)),
        )
    )
    db = _session()
    _seed_healthy_reference(
        provider, db, tenant_id=tenant_id, prefix=prefix, moment=dry_run_time
    )

    events: list[str] = []
    planned_keys: set[str] = set()

    def fake_delete(*, scope, key, expected_provider_code):
        # The raw key must already be durable (planned) BEFORE any delete --
        # this is the exact gap the planned-row fix closes.
        assert key in planned_keys, "delete ran before its key was planned"
        events.append(f"delete:{key}")

    def fake_planned(_db, *, tenant_id, run_id, keys):
        planned_keys.update(keys)
        events.append("planned")

    def failing_outcome(_db, **_kwargs):
        events.append("record_outcome_raises")
        raise RuntimeError("records database unavailable")

    monkeypatch.setattr(task_module, "delete_reviewed_file_orphan", fake_delete)

    dry_run_summary, apply_id = _run_dry_run_then_apply(
        monkeypatch,
        task_module,
        tenant_id=tenant_id,
        scope=scope,
        provider=provider,
        db=db,
        dry_run_time=dry_run_time,
        apply_time=apply_time,
    )
    monkeypatch.setattr(task_module, "record_cleanup_keys_planned", fake_planned)
    monkeypatch.setattr(task_module, "record_cleanup_key_outcome", failing_outcome)

    with pytest.raises(RuntimeError, match="records database unavailable"):
        task_module.clean_tenant_file_objects.run(
            str(apply_id),
            apply=True,
            actor="operator@example.com",
            expected_plan_digest=dry_run_summary["plan_digest"],
            reviewed_older_than=dry_run_summary["older_than"],
            reviewed_plan_observed_at=dry_run_summary["plan_observed_at"],
        )

    # Both keys were planned (durable) up front; only the first key's delete
    # ran before its outcome recording raised and the loop stopped there --
    # the second key was never even delete-attempted.
    assert planned_keys == {key_a, key_b}
    assert events == ["planned", f"delete:{key_a}", "record_outcome_raises"]


def test_clean_task_dry_run_never_records_anything(monkeypatch) -> None:
    """As above: ``report_file_objects`` always queries the real
    stored-file table, so the fake session must be a real, schema-bearing
    SQLite session, not a bare stand-in object."""
    task_module = importlib.import_module("app.tasks.file_object_reconciliation")
    provider = ListingProvider(())
    record_calls: list[str] = []
    db = _session()

    @contextmanager
    def scoped_session(_organization_id):
        yield db

    def fake_started(*_args, **_kwargs):
        record_calls.append("started")
        return uuid4()

    def fake_planned(*_args, **_kwargs):
        record_calls.append("planned")

    def fake_outcome(*_args, **_kwargs):
        record_calls.append("outcome")

    def fake_finished(*_args, **_kwargs):
        record_calls.append("finished")

    monkeypatch.setattr(task_module, "get_dotmac_files_read_provider", lambda: provider)
    monkeypatch.setattr(task_module, "list_objects", lambda _p, *, scope: ())
    monkeypatch.setattr(task_module, "session_for_org", scoped_session)
    monkeypatch.setattr(task_module, "record_cleanup_run_started", fake_started)
    monkeypatch.setattr(task_module, "record_cleanup_keys_planned", fake_planned)
    monkeypatch.setattr(task_module, "record_cleanup_key_outcome", fake_outcome)
    monkeypatch.setattr(task_module, "record_cleanup_run_finished", fake_finished)

    summary = task_module.clean_tenant_file_objects.run(str(uuid4()))

    assert summary["dry_run"] is True
    assert record_calls == []
