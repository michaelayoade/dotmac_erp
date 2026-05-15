"""Tests for get_db_for_org — the auth-aware DB dependency that primes
the session with the request's organization_id."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4


def test_get_db_for_org_primes_session_with_auth_org_id():
    """The dependency must call prime_session(db, auth.organization_id)
    before yielding the session."""
    from app.web.deps import get_db_for_org

    org_id = uuid4()
    auth = MagicMock(organization_id=org_id)

    # get_db_for_org is a generator dependency; the simplest test is to
    # call it directly and inspect the yielded session's info.
    gen = get_db_for_org(auth=auth)
    db = next(gen)
    try:
        assert db.info["organization_id"] == org_id
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_get_db_for_org_sets_postgres_rls_guc(monkeypatch):
    """The dependency must call set_current_organization_sync so the
    PostgreSQL GUC (app.current_organization_id) is set on the *same*
    session it yields. Without this, RLS-protected queries return empty
    rows and audit_log INSERTs (pre Bug A's per-row pin) tripped
    InsufficientPrivilege.
    """
    from app.web import deps as web_deps

    org_id = uuid4()
    auth = MagicMock(organization_id=org_id)
    calls: list[tuple[object, object]] = []

    monkeypatch.setattr(
        web_deps,
        "set_current_organization_sync",
        lambda db, org: calls.append((db, org)),
    )

    gen = web_deps.get_db_for_org(auth=auth)
    db = next(gen)
    try:
        assert len(calls) == 1, "set_current_organization_sync must run once"
        called_db, called_org = calls[0]
        assert called_db is db, (
            "GUC must be set on the same session that's yielded — "
            "calling it on a different session is the original Bug A pattern"
        )
        assert called_org == org_id
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_get_db_for_org_skips_rls_guc_when_no_org(monkeypatch):
    """When auth has no organization (e.g. pre-org-selection state),
    set_current_organization_sync must be skipped — passing None would
    trip the UUID validator. prime_session still runs (it tolerates None
    and the marker is just session.info)."""
    from app.web import deps as web_deps

    auth = MagicMock(organization_id=None)
    calls: list[tuple[object, object]] = []

    monkeypatch.setattr(
        web_deps,
        "set_current_organization_sync",
        lambda db, org: calls.append((db, org)),
    )

    gen = web_deps.get_db_for_org(auth=auth)
    next(gen)
    try:
        assert calls == [], "set_current_organization_sync must not run with org=None"
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_get_db_for_org_closes_session_on_completion():
    """The session must be closed when the generator exhausts.

    SQLAlchemy 2.0's ``Session.is_active`` stays True after ``close()``
    because of autobegin semantics, so we verify closure by spying on
    the ``close()`` method's invocation count (same approach as Task 3's
    ``session_for_org`` test).
    """
    from app.web.deps import get_db_for_org

    org_id = uuid4()
    auth = MagicMock(organization_id=org_id)

    gen = get_db_for_org(auth=auth)
    db = next(gen)

    # Wrap close() to count invocations.
    original_close = db.close
    close_calls = {"count": 0}

    def _spy_close(*args, **kwargs):
        close_calls["count"] += 1
        return original_close(*args, **kwargs)

    db.close = _spy_close

    # Drive the generator to completion so its finally-block runs.
    try:
        next(gen)
    except StopIteration:
        pass

    assert close_calls["count"] == 1
    assert db.in_transaction() is False
