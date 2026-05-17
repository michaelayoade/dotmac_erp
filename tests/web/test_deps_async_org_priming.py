"""Tests for get_async_db_for_org — the async sibling of get_db_for_org.

Same contract as the sync version (primes session.info, sets the PG GUC,
raises 403 on missing org context). These are unit tests at the dep level:
we mock AsyncSessionLocal to avoid pulling in a real AsyncEngine (the
SQLite test rig doesn't support async).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException


async def test_get_async_db_for_org_primes_session_with_auth_org_id(monkeypatch):
    """``prime_session`` must run against ``db.sync_session`` so the
    ORM listener (which attaches to sync Session events) reads the
    marker at flush time. Writing it on the AsyncSession proxy works
    by accident — ``info`` is the same dict — but priming the sync
    session is the explicit contract.
    """
    from app.web import deps as web_deps

    org_id = uuid4()
    auth = MagicMock(organization_id=org_id)

    fake_sync = MagicMock()
    fake_sync.info = {}
    fake_async = MagicMock()
    fake_async.sync_session = fake_sync

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=fake_async)
    cm.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(web_deps, "AsyncSessionLocal", lambda: cm)
    monkeypatch.setattr(web_deps, "set_current_organization", AsyncMock())

    gen = web_deps.get_async_db_for_org(auth=auth)
    db = await gen.__anext__()

    assert db is fake_async
    assert fake_sync.info["organization_id"] == org_id

    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()


async def test_get_async_db_for_org_sets_postgres_rls_guc(monkeypatch):
    """The awaitable ``set_current_organization`` must run on the same
    AsyncSession that's yielded. Without the GUC, RLS-protected async
    queries return empty rows even though the ORM listener is satisfied.
    """
    from app.web import deps as web_deps

    org_id = uuid4()
    auth = MagicMock(organization_id=org_id)

    fake_sync = MagicMock()
    fake_sync.info = {}
    fake_async = MagicMock()
    fake_async.sync_session = fake_sync

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=fake_async)
    cm.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(web_deps, "AsyncSessionLocal", lambda: cm)

    guc_calls: list[tuple[object, object]] = []

    async def _fake_set(db, org):
        guc_calls.append((db, org))

    monkeypatch.setattr(web_deps, "set_current_organization", _fake_set)

    gen = web_deps.get_async_db_for_org(auth=auth)
    db = await gen.__anext__()
    try:
        assert len(guc_calls) == 1, "set_current_organization must run once"
        called_db, called_org = guc_calls[0]
        assert called_db is db, (
            "GUC must be set on the same AsyncSession that's yielded — "
            "calling it on a different session is the original Bug A pattern"
        )
        assert called_org == org_id
    finally:
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()


async def test_get_async_db_for_org_raises_when_no_org_context(monkeypatch):
    """Mirrors the sync guard: an org-scoped async route reached without
    an org context is a programming bug — fail loudly with 403 rather
    than silently downgrade to an unprimed session whose RLS queries
    return empty rows."""
    from app.web import deps as web_deps

    auth = MagicMock(organization_id=None)

    guc_calls: list[tuple[object, object]] = []

    async def _fake_set(db, org):  # pragma: no cover - guard prevents call
        guc_calls.append((db, org))

    monkeypatch.setattr(web_deps, "set_current_organization", _fake_set)

    sessions_opened: list[object] = []
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(side_effect=lambda: sessions_opened.append(object()))
    cm.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(web_deps, "AsyncSessionLocal", lambda: cm)

    gen = web_deps.get_async_db_for_org(auth=auth)
    with pytest.raises(HTTPException) as excinfo:
        await gen.__anext__()

    assert excinfo.value.status_code == 403
    assert "Organization" in excinfo.value.detail
    assert guc_calls == [], "GUC setter must not run when org is missing"
    assert sessions_opened == [], (
        "AsyncSessionLocal must not be opened before the guard fires; "
        "otherwise the context-manager exit would run on a session that "
        "was never yielded"
    )


async def test_get_async_db_for_org_closes_session_via_async_with(monkeypatch):
    """The ``async with AsyncSessionLocal() as db:`` block must run
    ``__aexit__`` on completion so the underlying engine releases the
    connection back to the pool.
    """
    from app.web import deps as web_deps

    org_id = uuid4()
    auth = MagicMock(organization_id=org_id)

    fake_sync = MagicMock()
    fake_sync.info = {}
    fake_async = MagicMock()
    fake_async.sync_session = fake_sync

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=fake_async)
    cm.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(web_deps, "AsyncSessionLocal", lambda: cm)
    monkeypatch.setattr(web_deps, "set_current_organization", AsyncMock())

    gen = web_deps.get_async_db_for_org(auth=auth)
    await gen.__anext__()
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()

    cm.__aenter__.assert_awaited_once()
    cm.__aexit__.assert_awaited_once()
