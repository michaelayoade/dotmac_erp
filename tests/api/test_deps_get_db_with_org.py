"""Tests for ``get_db_with_org`` — the auth-aware DB dependency for API
routes that primes the session with the request's organization_id in
both Python-side (session.info) and PostgreSQL-side (GUC) form.

Locks the fix for the API-route arm of "Bug A" (dual-session pattern
silently returning empty rows from RLS-protected tables) in place.
"""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4


def test_get_db_with_org_primes_session_info():
    """The dependency must call prime_session(db, auth.organization_id)
    so the ORM listener can auto-filter ORM SELECTs by org_id."""
    from app.api.deps import get_db_with_org

    org_id = uuid4()
    auth = {"organization_id": str(org_id), "person_id": str(uuid4())}

    gen = get_db_with_org(auth=auth)
    db = next(gen)
    try:
        assert db.info["organization_id"] == org_id
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_get_db_with_org_sets_postgres_rls_guc(monkeypatch):
    """The dependency must call set_current_organization_sync on the
    *same* session it yields. This is the core Bug A fix — without it,
    RLS-protected SELECTs on the route's session return zero rows.
    """
    from app.api import deps as api_deps

    org_id = uuid4()
    auth = {"organization_id": str(org_id), "person_id": str(uuid4())}
    calls: list[tuple[object, object]] = []

    monkeypatch.setattr(
        api_deps,
        "set_current_organization_sync",
        lambda db, org: calls.append((db, org)),
    )

    gen = api_deps.get_db_with_org(auth=auth)
    db = next(gen)
    try:
        assert len(calls) == 1
        called_db, called_org = calls[0]
        assert called_db is db, (
            "GUC must be set on the same session that's yielded — "
            "calling it on a different session is the original Bug A"
        )
        assert called_org == org_id
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_get_db_with_org_rejects_missing_org_id():
    """An auth dict with no organization_id is a programming bug — the
    route should not have been allowed past require_tenant_auth. We
    raise 403 defensively rather than silently yielding an un-primed
    session."""
    from fastapi import HTTPException

    from app.api.deps import get_db_with_org

    auth: dict = {"person_id": str(uuid4())}  # no organization_id

    gen = get_db_with_org(auth=auth)
    try:
        next(gen)
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("Expected HTTPException(403) for missing org")


def test_get_db_with_org_auto_commits_on_yield_completion(monkeypatch):
    """Preserves the auto-commit semantics of the per-module ``get_db``
    that this dependency replaces. Migrations would silently change
    route semantics if commit didn't run after the yield."""
    from app.api import deps as api_deps

    fake_session = MagicMock()
    monkeypatch.setattr(api_deps, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(api_deps, "prime_session", lambda db, org: None)
    monkeypatch.setattr(api_deps, "set_current_organization_sync", lambda db, org: None)

    auth = {"organization_id": str(uuid4()), "person_id": str(uuid4())}
    gen = api_deps.get_db_with_org(auth=auth)
    next(gen)
    try:
        next(gen)
    except StopIteration:
        pass

    fake_session.commit.assert_called_once()
    fake_session.close.assert_called_once()
    fake_session.rollback.assert_not_called()


def test_get_db_with_org_rolls_back_on_exception(monkeypatch):
    """Mirrors the rollback-on-exception behavior of the per-module
    ``get_db`` it replaces. The migration must not change this."""
    from app.api import deps as api_deps

    fake_session = MagicMock()
    monkeypatch.setattr(api_deps, "SessionLocal", lambda: fake_session)
    monkeypatch.setattr(api_deps, "prime_session", lambda db, org: None)
    monkeypatch.setattr(api_deps, "set_current_organization_sync", lambda db, org: None)

    auth = {"organization_id": str(uuid4()), "person_id": str(uuid4())}
    gen = api_deps.get_db_with_org(auth=auth)
    next(gen)
    try:
        gen.throw(RuntimeError("simulated route failure"))
    except RuntimeError:
        pass

    fake_session.rollback.assert_called_once()
    fake_session.commit.assert_not_called()
    fake_session.close.assert_called_once()
