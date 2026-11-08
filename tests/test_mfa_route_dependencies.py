import inspect

from app.api import auth_flow
from app.api.deps import get_db_auth_bypass, get_db_with_org, require_tenant_auth


def test_mfa_enrollment_uses_tenant_scoped_dependencies():
    for endpoint in (auth_flow.mfa_setup, auth_flow.mfa_confirm):
        parameters = inspect.signature(endpoint).parameters
        assert parameters["auth"].default.dependency is require_tenant_auth
        assert parameters["db"].default.dependency is get_db_with_org


def test_mfa_verification_preserves_bootstrap_dependency():
    parameters = inspect.signature(auth_flow.mfa_verify).parameters
    assert "auth" not in parameters
    assert parameters["db"].default.dependency is get_db_auth_bypass


def test_tenant_auth_discovers_person_inside_scoped_sessions(monkeypatch):
    from contextlib import contextmanager
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from uuid import uuid4

    from app.services import auth_dependencies as deps

    first, second, person_id, session_id = (uuid4() for _ in range(4))
    db = MagicMock()
    db.scalar.return_value = SimpleNamespace(
        last_seen_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    monkeypatch.setattr(
        deps,
        "decode_access_token",
        lambda *_: {
            "sub": str(person_id),
            "session_id": str(session_id),
            "organization_id": str(first),
        },
    )
    monkeypatch.setattr(deps, "organization_ids", lambda **_: [first, second])
    visited = []

    @contextmanager
    def scoped(candidate):
        visited.append(candidate)
        session = MagicMock()
        session.scalar.return_value = second if candidate == second else None
        yield session

    monkeypatch.setattr(deps, "session_for_org", scoped)
    prime = MagicMock()
    monkeypatch.setattr(deps, "prime_tenant_context", prime)
    result = deps.require_tenant_auth(
        authorization="Bearer token", access_token=None, db=db
    )
    assert result["organization_id"] == str(second)
    assert visited == [first, second]
    db.get.assert_not_called()
    prime.assert_called_once_with(db, second)


def test_tenant_auth_does_not_assign_default_org_to_missing_person(monkeypatch):
    from unittest.mock import MagicMock
    from uuid import uuid4

    import pytest
    from fastapi import HTTPException

    from app.services import auth_dependencies as deps

    db = MagicMock()
    monkeypatch.setattr(
        deps,
        "decode_access_token",
        lambda *_: {
            "sub": str(uuid4()),
            "session_id": str(uuid4()),
        },
    )
    monkeypatch.setattr(deps, "is_session_inactive", lambda *_: False)
    monkeypatch.setattr(deps, "organization_ids", lambda **_: [])
    monkeypatch.setattr(deps.app_settings, "default_organization_id", str(uuid4()))
    with pytest.raises(HTTPException) as exc:
        deps.require_tenant_auth(authorization="Bearer token", access_token=None, db=db)
    assert exc.value.status_code == 403


def test_mfa_database_key_is_global_during_enrollment_and_verification(
    db_session, person, monkeypatch
):
    import pyotp
    from cryptography.fernet import Fernet
    from sqlalchemy import insert
    from starlette.requests import Request

    from app.models.auth import MFAMethod
    from app.models.domain_settings import (
        DomainSetting,
        SettingDomain,
        SettingValueType,
    )
    from app.services import auth_flow as service

    monkeypatch.delenv("TOTP_ENCRYPTION_KEY", raising=False)
    for organization_id in (None, person.organization_id):
        db_session.execute(
            insert(DomainSetting).values(
                domain=SettingDomain.auth,
                key="totp_encryption_key",
                organization_id=organization_id,
                value_type=SettingValueType.string,
                value_text=Fernet.generate_key().decode(),
                is_active=True,
            )
        )
    db_session.commit()
    db_session.info["organization_id"] = person.organization_id
    setup = service.AuthFlow.mfa_setup(db_session, str(person.id), "Phone")
    method = service.AuthFlow.mfa_confirm(
        db_session,
        str(setup["method_id"]),
        pyotp.TOTP(setup["secret"]).now(),
        str(person.id),
    )
    assert method.enabled and method.is_primary
    assert db_session.get(MFAMethod, setup["method_id"]).person_id == person.id
    db_session.info.pop("organization_id")
    token = service._issue_mfa_token(db_session, str(person.id))
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/auth/mfa/verify",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
    )
    tokens = service.AuthFlow.mfa_verify(
        db_session, token, pyotp.TOTP(setup["secret"]).now(), request
    )
    assert tokens["access_token"] and tokens["refresh_token"]
