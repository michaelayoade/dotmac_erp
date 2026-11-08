"""MFA HTTP enrollment under PostgreSQL forced RLS and a non-bypass role.

Run against a migrated disposable database with:
poetry run pytest tests/integration/test_mfa_tenant_rls.py -m integration -v
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pyotp
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture
def postgresql_scope_writers(monkeypatch):
    from app.db import session_context

    # The parent unit conftest stubs app.rls. Exercise its real PostgreSQL writers.
    spec = importlib.util.spec_from_file_location(
        "_mfa_real_rls", Path(__file__).resolve().parents[2] / "app/rls.py"
    )
    assert spec is not None and spec.loader is not None
    real_rls = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real_rls)
    for name in (
        "set_current_organization_on_connection",
        "set_current_organization_sync",
    ):
        monkeypatch.setattr(session_context, name, getattr(real_rls, name))


@pytest.fixture
def mfa_runtime(engine, monkeypatch, postgresql_scope_writers):
    import app.db
    from app.api import auth_flow, deps
    from app.models.auth import SessionStatus
    from app.models.auth import Session as AuthSession
    from app.models.finance.core_org.organization import Organization
    from app.models.person import Person
    from app.services import auth_dependencies, auth_flow as service

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            assert connection.scalar(
                text(
                    "SELECT relrowsecurity AND relforcerowsecurity "
                    "FROM pg_class WHERE oid = 'public.people'::regclass"
                )
            ), "This canary requires migrated, forced RLS on people"
            org_a, org_b = uuid4(), uuid4()
            alice, colleague, bob = uuid4(), uuid4(), uuid4()
            alice_session = uuid4()
            with Session(connection, join_transaction_mode="create_savepoint") as seed:
                for org_id in (org_a, org_b):
                    seed.add(
                        Organization(
                            organization_id=org_id,
                            organization_code=f"MFA-{org_id.hex[:12]}",
                            legal_name="MFA RLS canary",
                            functional_currency_code="NGN",
                            presentation_currency_code="NGN",
                            fiscal_year_end_month=12,
                            fiscal_year_end_day=31,
                        )
                    )
                seed.flush()
                for person_id, org_id in (
                    (alice, org_a),
                    (colleague, org_a),
                    (bob, org_b),
                ):
                    seed.add(
                        Person(
                            id=person_id,
                            organization_id=org_id,
                            first_name="MFA",
                            last_name="Canary",
                            email=f"{person_id}@example.com",
                        )
                    )
                seed.flush()
                seed.add(
                    AuthSession(
                        id=alice_session,
                        person_id=alice,
                        status=SessionStatus.active,
                        token_hash=uuid4().hex,
                        last_seen_at=datetime.now(timezone.utc),
                        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                    )
                )
                seed.commit()

            connection.execute(text("SET LOCAL ROLE app_user"))
            assert not connection.scalar(
                text(
                    "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
                )
            )

            def clear_scope():
                connection.execute(
                    text(
                        "SELECT set_config('app.current_organization_id', '', true), "
                        "set_config('app.current_tenant', '', true)"
                    )
                )

            clear_scope()
            with Session(connection, join_transaction_mode="create_savepoint") as probe:
                assert probe.scalar(select(Person.id).where(Person.id == alice)) is None

            def session_factory():
                return Session(connection, join_transaction_mode="create_savepoint")

            monkeypatch.setattr(app.db, "SessionLocal", session_factory)
            monkeypatch.setattr(deps, "SessionLocal", session_factory)
            monkeypatch.setattr(auth_dependencies, "SessionLocal", session_factory)
            # Limit discovery to this transaction's fixtures; data reads retain real RLS.
            monkeypatch.setattr(
                auth_dependencies, "organization_ids", lambda **_: [org_a, org_b]
            )
            monkeypatch.setattr(
                auth_dependencies.app_settings, "default_organization_id", None
            )
            audit_calls = []

            def audit(**kwargs):
                db = kwargs["db"]
                assert db.scalar(select(Person.id).where(Person.id == alice)) == alice
                assert db.scalar(
                    text("SELECT current_setting('app.current_organization_id')")
                ) == str(org_a)
                assert db.scalar(
                    text("SELECT current_setting('app.current_tenant')")
                ) == str(org_a)
                audit_calls.append(kwargs)

            monkeypatch.setattr("app.services.audit_dispatcher.fire_audit_event", audit)
            app = FastAPI()
            app.include_router(auth_flow.router, prefix="/api/v1")
            with Session(
                connection, join_transaction_mode="create_savepoint"
            ) as token_db:
                token = service._issue_access_token(
                    token_db, str(alice), str(alice_session)
                )
            headers = {"Authorization": f"Bearer {token}"}
            with TestClient(app) as client:
                yield {
                    "client": client,
                    "headers": headers,
                    "alice": alice,
                    "colleague": colleague,
                    "bob": bob,
                    "org_a": org_a,
                    "org_b": org_b,
                    "session_factory": session_factory,
                    "clear_scope": clear_scope,
                    "audit_calls": audit_calls,
                    "service": service,
                }
        finally:
            transaction.rollback()


def test_enrollment_confirmation_and_prelogin_verification(mfa_runtime):
    runtime = mfa_runtime
    client = runtime["client"]
    runtime["clear_scope"]()
    response = client.post(
        "/api/v1/auth/mfa/setup",
        headers=runtime["headers"],
        json={
            "person_id": str(runtime["alice"]),
            "label": "Canary phone",
        },
    )
    assert response.status_code == 200, response.text
    setup = response.json()
    assert {"method_id", "secret", "otpauth_uri"} <= setup.keys()
    totp = pyotp.TOTP(setup["secret"])
    invalid_code = f"{(int(totp.now()) + 1) % 1000000:06d}"
    runtime["clear_scope"]()
    response = client.post(
        "/api/v1/auth/mfa/confirm",
        headers=runtime["headers"],
        json={"method_id": setup["method_id"], "code": invalid_code},
    )
    assert response.status_code == 401, response.text
    runtime["clear_scope"]()
    response = client.post(
        "/api/v1/auth/mfa/confirm",
        headers=runtime["headers"],
        json={
            "method_id": setup["method_id"],
            "code": pyotp.TOTP(setup["secret"]).now(),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["person_id"] == str(runtime["alice"])
    assert response.json()["enabled"] and response.json()["is_primary"]
    assert runtime["audit_calls"][0]["organization_id"] == runtime["org_a"]

    # Only the short-lived MFA challenge is supplied; no access-token header.
    runtime["clear_scope"]()
    with runtime["session_factory"]() as db:
        challenge = runtime["service"]._issue_mfa_token(db, str(runtime["alice"]))
    response = client.post(
        "/api/v1/auth/mfa/verify",
        json={
            "mfa_token": challenge,
            "code": pyotp.TOTP(setup["secret"]).now(),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["access_token"]
    assert response.cookies.get("refresh_token")


@pytest.mark.parametrize("target", ["colleague", "bob"])
def test_enrollment_cannot_target_another_person_or_tenant(mfa_runtime, target):
    from app.db.session_context import tenant_scope_for_session

    runtime = mfa_runtime
    runtime["clear_scope"]()
    response = runtime["client"].post(
        "/api/v1/auth/mfa/setup",
        headers=runtime["headers"],
        json={"person_id": str(runtime[target]), "label": "Forbidden"},
    )
    assert response.status_code == 403
    target_org = runtime["org_a"] if target == "colleague" else runtime["org_b"]
    with runtime["session_factory"]() as db:
        with tenant_scope_for_session(db, target_org):
            other = runtime["service"].AuthFlow.mfa_setup(
                db, str(runtime[target]), "Other"
            )
    runtime["clear_scope"]()
    response = runtime["client"].post(
        "/api/v1/auth/mfa/confirm",
        headers=runtime["headers"],
        json={
            "method_id": str(other["method_id"]),
            "code": pyotp.TOTP(other["secret"]).now(),
        },
    )
    assert response.status_code == 404


def test_tenant_scope_survives_real_commit(engine, postgresql_scope_writers):
    from app.db.session_context import tenant_scope_for_session

    organization_id = uuid4()
    with engine.connect() as connection:
        connection.execute(text("SET ROLE app_user"))
        connection.commit()
        try:
            with Session(connection) as db:
                with tenant_scope_for_session(db, organization_id):
                    for _ in range(2):
                        assert db.scalar(
                            text(
                                "SELECT current_setting('app.current_organization_id')"
                            )
                        ) == str(organization_id)
                        assert db.scalar(
                            text("SELECT current_setting('app.current_tenant')")
                        ) == str(organization_id)
                        db.commit()
        finally:
            connection.execute(text("RESET ROLE"))
            connection.commit()
