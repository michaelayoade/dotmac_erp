"""Lock ownership must survive ORM commits, interruption and retry exhaustion."""

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
    monkeypatch.setattr(
        dotmac_sub, "_try_acquire_incremental_sync_lock", lambda *_: False
    )
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
    monkeypatch.setattr(
        dotmac_sub, "_try_acquire_incremental_sync_lock", lambda *_: True
    )
    monkeypatch.setattr(dotmac_sub, "_release_incremental_sync_lock", release)
    monkeypatch.setattr(dotmac_sub, "_running_incremental_history_id", lambda *_: None)
    monkeypatch.setattr(
        dotmac_sub, "_build_sync_context", lambda *_: (service, history, org)
    )
    monkeypatch.setattr(dotmac_sub, "chain", MagicMock())
    with pytest.raises(RuntimeError, match="service cleanup"):
        dotmac_sub._enqueue_incremental_sync_workflow(MagicMock(), org, 1)
    release.assert_called_once_with(db, org)
