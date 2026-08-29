from __future__ import annotations

from contextlib import nullcontext
from unittest.mock import MagicMock
from uuid import uuid4

from app.tasks import dotmac_sub


def test_incremental_sync_has_bounded_execution_time() -> None:
    task = dotmac_sub.run_dotmac_sub_incremental_sync

    assert task.soft_time_limit == 25 * 60
    assert task.time_limit == 28 * 60


def test_incremental_sync_skips_when_single_flight_lock_is_held(monkeypatch) -> None:
    organization_id = uuid4()
    db = MagicMock()
    build_context = MagicMock()

    monkeypatch.setattr(
        dotmac_sub,
        "session_for_org",
        lambda _organization_id: nullcontext(db),
    )
    monkeypatch.setattr(
        dotmac_sub,
        "_try_acquire_incremental_sync_lock",
        lambda _db, _organization_id: False,
    )
    monkeypatch.setattr(dotmac_sub, "_build_sync_context", build_context)

    result = dotmac_sub.run_dotmac_sub_incremental_sync.run(str(organization_id))

    assert result == {
        "success": True,
        "organization_id": str(organization_id),
        "skipped": True,
        "reason": "already_running",
    }
    build_context.assert_not_called()


def test_incremental_sync_lock_is_transaction_scoped() -> None:
    organization_id = uuid4()
    db = MagicMock()
    db.scalar.return_value = True

    assert dotmac_sub._try_acquire_incremental_sync_lock(db, organization_id) is True

    statement = str(db.scalar.call_args.args[0])
    assert "pg_try_advisory_xact_lock" in statement
    assert "hashtextextended" in statement
    assert db.scalar.call_args.args[1] == {
        "lock_identity": f"dotmac_sub:incremental:{organization_id}"
    }
