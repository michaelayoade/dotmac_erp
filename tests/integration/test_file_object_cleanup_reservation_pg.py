"""Two-backend PostgreSQL proof of the file cleanup/staging lock protocol.

Each contender has its own connection and transaction. The first three tests
hold an uncommitted write, observe the second backend blocked on its advisory
lock, commit, then prove the SAME waiting transaction sees the committed row
and refuses the operation. The last test proves sorted multi-key acquisition
from PostgreSQL's live advisory-lock state.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from queue import Queue
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from dotmac_files import PreparedFile
from dotmac_kernel.cache import TenantScope
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.file_object_cleanup import (
    CleanupUnsafeReferenceView,
    record_cleanup_keys_planned,
    reserve_cleanup_keys,
    stage_tenant_file_if_unreserved,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def reservation_tenant(engine):
    """Commit one disposable identity so two independent backends can see it."""
    tenant_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO core_org.organization "
                "(organization_id, organization_code, legal_name, "
                "functional_currency_code, presentation_currency_code, "
                "fiscal_year_end_month, fiscal_year_end_day, is_active) "
                "VALUES (:id, :code, 'File reservation test', "
                "'NGN', 'NGN', 12, 31, true)"
            ),
            {"id": tenant_id, "code": f"FR-{tenant_id.hex[:12].upper()}"},
        )
        connection.execute(
            text(
                "INSERT INTO public.tenants (id, slug, name, is_active) "
                "VALUES (:id, :slug, 'File reservation test', true)"
            ),
            {"id": tenant_id, "slug": f"erp-{tenant_id}"},
        )
    try:
        yield tenant_id
    finally:
        with engine.begin() as connection:
            _set_tenant_context(connection, tenant_id)
            connection.execute(
                text(
                    "DELETE FROM public.file_orphan_cleanup_deletions "
                    "WHERE organization_id = :id"
                ),
                {"id": tenant_id},
            )
            connection.execute(
                text(
                    "DELETE FROM public.file_orphan_cleanup_runs "
                    "WHERE organization_id = :id"
                ),
                {"id": tenant_id},
            )
            connection.execute(
                text("DELETE FROM mod_files.stored_files WHERE tenant_id = :id"),
                {"id": tenant_id},
            )
            connection.execute(
                text("DELETE FROM public.tenants WHERE id = :id"),
                {"id": tenant_id},
            )
            connection.execute(
                text("DELETE FROM core_org.organization WHERE organization_id = :id"),
                {"id": tenant_id},
            )


def _set_tenant_context(connection, tenant_id: UUID) -> None:
    connection.execute(
        text("SELECT set_config('app.current_organization_id', :id, true)"),
        {"id": str(tenant_id)},
    )
    connection.execute(
        text("SELECT set_config('app.current_tenant', :id, true)"),
        {"id": str(tenant_id)},
    )


def _backend_pid(connection) -> int:
    pid = connection.scalar(text("SELECT pg_backend_pid()"))
    assert isinstance(pid, int)
    return pid


@contextmanager
def _runtime_session(engine, tenant_id: UUID, *, lock_timeout: str = "300ms"):
    connection = engine.connect()
    transaction = connection.begin()
    try:
        connection.execute(text("SET LOCAL ROLE app_user"))
        _set_tenant_context(connection, tenant_id)
        connection.execute(
            text("SELECT set_config('lock_timeout', :timeout, true)"),
            {"timeout": lock_timeout},
        )
        with Session(bind=connection) as db:
            yield connection, db, transaction
    finally:
        if transaction.is_active:
            transaction.rollback()
        connection.close()


def _prepared(tenant_id: UUID) -> PreparedFile:
    file_id = uuid4()
    return PreparedFile(
        id=file_id,
        scope=TenantScope(tenant_id),
        provider_code="erp_s3",
        storage_key=f"tenants/{tenant_id}/files/{file_id}",
        original_filename="import.csv",
        size_bytes=1,
        declared_media_type="text/csv",
        detected_media_type="text/csv",
        checksum_sha256="sha256:" + "a" * 64,
    )


def _planned(db: Session, tenant_id: UUID, key: str) -> None:
    run_id = uuid4()
    db.execute(
        text(
            "INSERT INTO public.file_orphan_cleanup_runs "
            "(id, organization_id, plan_digest, older_than, plan_observed_at, "
            "provider_code, candidate_count, actor, invocation_id, status) "
            "VALUES (:run_id, :tenant_id, :digest, :older_than, :observed_at, "
            "'erp_s3', 1, 'integration-test', :invocation_id, 'running')"
        ),
        {
            "run_id": run_id,
            "tenant_id": tenant_id,
            "digest": "a" * 64,
            "older_than": datetime(2026, 9, 1, tzinfo=UTC),
            "observed_at": datetime(2026, 9, 20, tzinfo=UTC),
            "invocation_id": str(uuid4()),
        },
    )
    record_cleanup_keys_planned(db, tenant_id=tenant_id, run_id=run_id, keys=(key,))


def _assert_waiter_refuses_after_commit(
    engine,
    tenant_id: UUID,
    first_connection,
    first_transaction,
    attempt: Callable[[Session], None],
    expected_message: str,
) -> None:
    pid_queue: Queue[int] = Queue()

    def contender() -> None:
        with _runtime_session(engine, tenant_id, lock_timeout="10s") as (
            connection,
            db,
            _,
        ):
            pid_queue.put(_backend_pid(connection))
            with pytest.raises(CleanupUnsafeReferenceView, match=expected_message):
                attempt(db)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(contender)
        try:
            waiter_pid = pid_queue.get(timeout=3)
            blocker_pid = _backend_pid(first_connection)
            assert waiter_pid != blocker_pid
            _wait_for_advisory_block(first_connection, waiter_pid, blocker_pid)
        finally:
            first_transaction.commit()
        future.result(timeout=5)


def test_writer_commit_makes_waiting_cleanup_refuse_reference(
    engine, reservation_tenant
):
    tenant_id = reservation_tenant
    prepared = _prepared(tenant_id)
    with _runtime_session(engine, tenant_id) as (writer_conn, writer, writer_tx):
        stage_tenant_file_if_unreserved(writer, prepared=prepared)
        _assert_waiter_refuses_after_commit(
            engine,
            tenant_id,
            writer_conn,
            writer_tx,
            lambda db: reserve_cleanup_keys(
                db, tenant_id=tenant_id, keys=(prepared.storage_key,)
            ),
            "gained a reference",
        )


def test_cleanup_commit_makes_waiting_writer_refuse_reservation(
    engine, reservation_tenant
):
    tenant_id = reservation_tenant
    prepared = _prepared(tenant_id)
    with _runtime_session(engine, tenant_id) as (cleanup_conn, cleanup, cleanup_tx):
        reserve_cleanup_keys(cleanup, tenant_id=tenant_id, keys=(prepared.storage_key,))
        _planned(cleanup, tenant_id, prepared.storage_key)
        _assert_waiter_refuses_after_commit(
            engine,
            tenant_id,
            cleanup_conn,
            cleanup_tx,
            lambda db: stage_tenant_file_if_unreserved(db, prepared=prepared),
            "cleanup reservation",
        )


def test_second_apply_cannot_reserve_a_committed_key(engine, reservation_tenant):
    tenant_id = reservation_tenant
    key = _prepared(tenant_id).storage_key
    with _runtime_session(engine, tenant_id) as (first_conn, first, first_tx):
        reserve_cleanup_keys(first, tenant_id=tenant_id, keys=(key,))
        _planned(first, tenant_id, key)
        _assert_waiter_refuses_after_commit(
            engine,
            tenant_id,
            first_conn,
            first_tx,
            lambda db: reserve_cleanup_keys(db, tenant_id=tenant_id, keys=(key,)),
            "already has",
        )


def _wait_for_advisory_block(connection, waiter_pid: int, blocker_pid: int) -> None:
    deadline = monotonic() + 5
    while monotonic() < deadline:
        blocked = connection.scalar(
            text(
                "SELECT :blocker_pid = ANY(pg_blocking_pids(:waiter_pid)) "
                "AND EXISTS (SELECT 1 FROM pg_locks WHERE pid = :waiter_pid "
                "AND locktype = 'advisory' AND NOT granted)"
            ),
            {"waiter_pid": waiter_pid, "blocker_pid": blocker_pid},
        )
        if blocked:
            return
        sleep(0.02)
    raise AssertionError("second backend did not wait on an advisory lock")


def test_overlapping_keys_lock_in_sorted_order_without_deadlock(
    engine, reservation_tenant
):
    tenant_id = reservation_tenant
    key_a, key_b, key_c = sorted(_prepared(tenant_id).storage_key for _ in range(3))
    pid_queue: Queue[int] = Queue()

    def contender() -> None:
        with _runtime_session(engine, tenant_id, lock_timeout="10s") as (
            connection,
            db,
            _,
        ):
            pid_queue.put(_backend_pid(connection))
            reserve_cleanup_keys(db, tenant_id=tenant_id, keys=(key_b, key_a))

    with _runtime_session(engine, tenant_id, lock_timeout="10s") as (
        connection,
        db,
        first_tx,
    ):
        reserve_cleanup_keys(db, tenant_id=tenant_id, keys=(key_c, key_b))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(contender)
            try:
                waiter_pid = pid_queue.get(timeout=3)
                _wait_for_advisory_block(
                    connection, waiter_pid, _backend_pid(connection)
                )
                # The contender acquired a before waiting for b. If it had
                # followed caller order (b, a), this try-lock would succeed.
                got_a = connection.scalar(
                    text(
                        "SELECT pg_try_advisory_xact_lock("
                        "hashtextextended(:identity, 0))"
                    ),
                    {"identity": f"erp-file-orphan:{tenant_id}:{key_a}"},
                )
                assert got_a is False
            finally:
                first_tx.rollback()
            future.result(timeout=5)
