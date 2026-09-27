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
