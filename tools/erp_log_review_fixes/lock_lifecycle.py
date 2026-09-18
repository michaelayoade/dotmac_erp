"""Isolated patch recipe; this workspace helper is not included in the PR."""

BRANCH = "fix/erp-sync-lock-lifecycle"
TITLE = "fix(sync): pin advisory locks and finalise exhausted phase retries"
TESTS = ["tests/tasks/test_dotmac_sub_tasks.py", "tests/tasks/test_dotmac_sub_lock_lifecycle.py"]


def apply(change, write, rewrite):
    path = "app/tasks/dotmac_sub.py"
    change(path, '_INCREMENTAL_SYNC_LOCK_NAMESPACE = "dotmac_sub:incremental"', '_INCREMENTAL_SYNC_LOCK_NAMESPACE = "dotmac_sub:incremental"\n_INCREMENTAL_LOCK_CONNECTION_KEY = "dotmac_sub_incremental_lock_connection"')
    rewrite(path, "_try_acquire_incremental_sync_lock", lambda _: '''def _try_acquire_incremental_sync_lock(db: Session, organization_id: UUID) -> bool:
    """Own a session advisory lock on a dedicated physical connection.

    ORM commit/rollback returns its transaction connection to the pool. A
    PostgreSQL session lock outlives that transaction, so it must never be
    acquired through the ORM session. This connection executes lock SQL only;
    business queries keep their existing tenant-scoped session.
    """
    if _INCREMENTAL_LOCK_CONNECTION_KEY in db.info:
        raise RuntimeError("This session already owns an incremental sync lock")
    lock_identity = f"{_INCREMENTAL_SYNC_LOCK_NAMESPACE}:{organization_id}"
    connection = db.get_bind().engine.connect()
    acquired = False
    try:
        connection = connection.execution_options(isolation_level="AUTOCOMMIT")
        acquired = bool(
            connection.scalar(
                text("SELECT pg_try_advisory_lock(hashtextextended(:lock_identity, 0))"),
                {"lock_identity": lock_identity},
            )
        )
        if acquired:
            db.info[_INCREMENTAL_LOCK_CONNECTION_KEY] = (organization_id, connection)
        return acquired
    except BaseException:
        # The server may have acquired the lock before a connection error was
        # reported. Never put an uncertain lock owner back into the pool.
        acquired = False
        connection.invalidate()
        raise
    finally:
        if not acquired:
            connection.close()
''')
    rewrite(path, "_release_incremental_sync_lock", lambda _: '''def _release_incremental_sync_lock(db: Session, organization_id: UUID) -> bool:
    """Release on the acquiring backend, even if the ORM session is unusable."""
    owner = db.info.get(_INCREMENTAL_LOCK_CONNECTION_KEY)
    if owner is None:
        return False
    held_org_id, connection = owner
    if held_org_id != organization_id:
        raise RuntimeError("Incremental sync lock organization does not match its owner")
    db.info.pop(_INCREMENTAL_LOCK_CONNECTION_KEY)
    lock_identity = f"{_INCREMENTAL_SYNC_LOCK_NAMESPACE}:{organization_id}"
    try:
        released = bool(
            connection.scalar(
                text("SELECT pg_advisory_unlock(hashtextextended(:lock_identity, 0))"),
                {"lock_identity": lock_identity},
            )
        )
        if not released:
            raise RuntimeError("Incremental sync advisory unlock was not acknowledged")
        return True
    except BaseException:
        connection.invalidate()
        raise
    finally:
        connection.close()
''')
    def enqueue(source):
        old = '''        finally:
            if service is not None:
                service.close()
            try:
                _release_incremental_sync_lock(db, org_id)
            except Exception:
                logger.exception(
                    "Failed to release dotmac_sub incremental sync lock for org %s",
                    org_id,
                )'''
        new = '''        finally:
            try:
                if service is not None:
                    service.close()
            finally:
                try:
                    _release_incremental_sync_lock(db, org_id)
                except Exception:
                    logger.exception(
                        "Failed to release dotmac_sub incremental sync lock for org %s",
                        org_id,
                    )'''
        assert source.count(old) == 1
        return source.replace(old, new)
    rewrite(path, "_enqueue_incremental_sync_workflow", enqueue)
    def phase(source):
        old = '''            raise self.retry(
                exc=RuntimeError(
                    f"dotmac_sub incremental phase lock is held for org {org_id}"
                ),
                countdown=60,
            )'''
        new = '''            lock_error = RuntimeError(
                f"dotmac_sub incremental phase lock is held for org {org_id}"
            )
            if self.max_retries is not None and self.request.retries >= self.max_retries:
                _handle_sync_failure(
                    history_uuid, org_id, lock_error, "Incremental phase lock"
                )
            raise self.retry(exc=lock_error, countdown=60)'''
        assert source.count(old) == 1
        source = source.replace(old, new)
        source = source.replace("        session_usable = True\n", "")
        source = source.replace("session_usable = _rollback_interrupted_session(db)", "_rollback_interrupted_session(db)")
        old = '''        finally:
            if service is not None:
                service.close()
            if session_usable:
                try:
                    _release_incremental_sync_lock(db, org_id)
                except Exception:
                    logger.exception(
                        "Failed to release dotmac_sub incremental sync lock for org %s",
                        org_id,
                    )'''
        new = '''        finally:
            try:
                if service is not None:
                    service.close()
            finally:
                # Lock ownership is independent of the interrupted ORM transaction.
                try:
                    _release_incremental_sync_lock(db, org_id)
                except Exception:
                    logger.exception(
                        "Failed to release dotmac_sub incremental sync lock for org %s",
                        org_id,
                    )'''
        assert source.count(old) == 1
        return source.replace(old, new)
    rewrite(path, "run_dotmac_sub_incremental_sync_phase", phase)
    test_path = "tests/tasks/test_dotmac_sub_tasks.py"
    rewrite(test_path, "test_incremental_sync_lock_is_session_scoped", lambda _: '''def test_incremental_sync_lock_is_session_scoped() -> None:
    organization_id = uuid4()
    db = MagicMock()
    db.info = {}
    connection = db.get_bind.return_value.engine.connect.return_value
    connection.execution_options.return_value = connection
    connection.scalar.return_value = True

    assert dotmac_sub._try_acquire_incremental_sync_lock(db, organization_id) is True

    statement = str(connection.scalar.call_args.args[0])
    assert "pg_try_advisory_lock" in statement
    assert "pg_try_advisory_xact_lock" not in statement
    assert "hashtextextended" in statement
    assert connection.scalar.call_args.args[1] == {
        "lock_identity": f"dotmac_sub:incremental:{organization_id}"
    }
    db.scalar.assert_not_called()
    assert dotmac_sub._release_incremental_sync_lock(db, organization_id) is True
''')
    rewrite(test_path, "test_incremental_sync_lock_release_uses_same_identity", lambda _: '''def test_incremental_sync_lock_release_uses_same_identity() -> None:
    organization_id = uuid4()
    db = MagicMock()
    db.info = {}
    connection = db.get_bind.return_value.engine.connect.return_value
    connection.execution_options.return_value = connection
    connection.scalar.return_value = True

    assert dotmac_sub._try_acquire_incremental_sync_lock(db, organization_id) is True
    db.commit()
    db.rollback()
    assert dotmac_sub._release_incremental_sync_lock(db, organization_id) is True

    statement = str(connection.scalar.call_args.args[0])
    assert "pg_advisory_unlock" in statement
    assert connection.scalar.call_args.args[1] == {
        "lock_identity": f"dotmac_sub:incremental:{organization_id}"
    }
    connection.close.assert_called_once_with()
    db.scalar.assert_not_called()
''')
    write("tests/tasks/test_dotmac_sub_lock_lifecycle.py", '''"""Lock ownership must survive ORM commits, interruption and retry exhaustion."""

from contextlib import nullcontext
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.tasks import dotmac_sub


def owner():
    db = MagicMock()
    db.info = {}
    connection = db.get_bind.return_value.engine.connect.return_value
    connection.execution_options.return_value = connection
    connection.scalar.return_value = True
    return db, connection, uuid4()


def test_denied_lock_closes_connection_without_retaining_owner():
    db, connection, org = owner()
    connection.scalar.return_value = False
    assert dotmac_sub._try_acquire_incremental_sync_lock(db, org) is False
    assert db.info == {}
    connection.close.assert_called_once_with()


def test_acquisition_error_invalidates_uncertain_backend():
    db, connection, org = owner()
    connection.scalar.side_effect = RuntimeError("connection interrupted")
    with pytest.raises(RuntimeError, match="interrupted"):
        dotmac_sub._try_acquire_incremental_sync_lock(db, org)
    connection.invalidate.assert_called_once_with()
    connection.close.assert_called_once_with()
    assert db.info == {}


def test_false_unlock_is_not_silently_returned_to_pool():
    db, connection, org = owner()
    assert dotmac_sub._try_acquire_incremental_sync_lock(db, org)
    connection.scalar.return_value = False
    with pytest.raises(RuntimeError, match="not acknowledged"):
        dotmac_sub._release_incremental_sync_lock(db, org)
    connection.invalidate.assert_called_once_with()
    connection.close.assert_called_once_with()
    assert db.info == {}


def test_repeat_acquisition_cannot_stack_session_locks():
    db, connection, org = owner()
    assert dotmac_sub._try_acquire_incremental_sync_lock(db, org)
    with pytest.raises(RuntimeError, match="already owns"):
        dotmac_sub._try_acquire_incremental_sync_lock(db, org)
    assert connection.scalar.call_count == 1
    assert dotmac_sub._release_incremental_sync_lock(db, org)


def test_unlock_error_closes_owner_after_invalidation():
    db, connection, org = owner()
    assert dotmac_sub._try_acquire_incremental_sync_lock(db, org)
    connection.scalar.side_effect = RuntimeError("unlock interrupted")
    with pytest.raises(RuntimeError, match="unlock interrupted"):
        dotmac_sub._release_incremental_sync_lock(db, org)
    connection.invalidate.assert_called_once_with()
    connection.close.assert_called_once_with()
    assert db.info == {}


def test_retry_exhaustion_finalises_only_attempted_history(monkeypatch):
    org, history = uuid4(), uuid4()
    db = MagicMock()
    failure = MagicMock()
    monkeypatch.setattr(dotmac_sub, "session_for_org", lambda _: nullcontext(db))
    monkeypatch.setattr(dotmac_sub, "_try_acquire_incremental_sync_lock", lambda *_: False)
    monkeypatch.setattr(dotmac_sub, "_handle_sync_failure", failure)
    task = dotmac_sub.run_dotmac_sub_incremental_sync_phase
    task.push_request(retries=task.max_retries, called_directly=True)
    try:
        with pytest.raises(RuntimeError, match="phase lock is held"):
            task.run(str(org), str(history), "resellers")
    finally:
        task.pop_request()
    assert failure.call_count == 1
    assert failure.call_args.args[:2] == (history, org)
    assert failure.call_args.args[3] == "Incremental phase lock"


def test_service_cleanup_error_still_releases_lock(monkeypatch):
    org, history = uuid4(), uuid4()
    db, _, _ = owner()
    service = MagicMock()
    service.close.side_effect = RuntimeError("service cleanup")
    release = MagicMock(return_value=True)
    monkeypatch.setattr(dotmac_sub, "session_for_org", lambda _: nullcontext(db))
    monkeypatch.setattr(dotmac_sub, "_try_acquire_incremental_sync_lock", lambda *_: True)
    monkeypatch.setattr(dotmac_sub, "_release_incremental_sync_lock", release)
    monkeypatch.setattr(dotmac_sub, "_running_incremental_history_id", lambda *_: None)
    monkeypatch.setattr(dotmac_sub, "_build_sync_context", lambda *_: (service, history, org))
    monkeypatch.setattr(dotmac_sub, "chain", MagicMock())
    with pytest.raises(RuntimeError, match="service cleanup"):
        dotmac_sub._enqueue_incremental_sync_workflow(MagicMock(), org, 1)
    release.assert_called_once_with(db, org)
''')
    write("tests/integration/test_dotmac_sub_lock_connection.py", '''"""Real PostgreSQL proof that an ORM commit cannot release the lock connection."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.tasks.dotmac_sub import (
    _release_incremental_sync_lock,
    _try_acquire_incremental_sync_lock,
)


@pytest.mark.integration
def test_incremental_lock_survives_commit_and_releases_same_backend():
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("TEST_DATABASE_URL must point to a disposable PostgreSQL test database")
    engine = create_engine(url, pool_size=2, max_overflow=0)
    org = uuid4()
    identity = {"key": f"dotmac_sub:incremental:{org}"}
    acquire = text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))")
    release = text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))")
    try:
        with Session(engine) as db:
            assert _try_acquire_incremental_sync_lock(db, org)
            try:
                db.execute(text("SELECT 1"))
                db.commit()
                db.execute(text("SELECT 1"))
                db.rollback()
                with engine.connect() as competitor:
                    assert competitor.scalar(acquire, identity) is False
            finally:
                assert _release_incremental_sync_lock(db, org)
            with engine.connect() as competitor:
                assert competitor.scalar(acquire, identity) is True
                assert competitor.scalar(release, identity) is True
    finally:
        engine.dispose()
''')
