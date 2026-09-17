"""Integrator ProductPort receiver for Sub's invoice-accounting-sync observations.

Route-level tests. Scope-dependency behavior mirrors the direct-call pattern
already used by ``tests/sync/test_service_auth_scopes.py`` for the existing
``require_service_scope``/``require_any_service_scope`` helpers.

The write/mirror routes are the FIRST ERP callers of
``dotmac_kernel.idempotency`` (ADR-0001). ``tests/conftest.py``'s fixed
SQLite-compatible table whitelist predates this caller and does not create
the kernel's ``idempotency_records``/``platform_idempotency_records`` tables,
so this file creates them itself (scoped to this file's own fixture, not a
change to the shared conftest) rather than editing that shared, widely-used
fixture file.
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.integrator_observations import (
    SCOPE_MIRROR,
    SCOPE_WRITE,
    require_write_or_mirror_scope,
    require_write_scope,
    router,
)
from app.api.service_principal import get_db_with_service_org, require_service_auth
from app.models.finance.ar.dotmac_sub_invoice_sync_outcome import (
    DotmacSubInvoiceSyncOutcome,
)
from app.services.dotmac_sub.invoice_sync_outcomes import (
    record_invoice_sync_outcome as _real_record_invoice_sync_outcome,
)

ORG_ID = uuid4()
VALID_FINGERPRINT = "a" * 64


# ---------------------------------------------------------------------------
# Scope-dependency behavior — direct calls, no HTTP/DB needed (matches the
# existing convention in tests/sync/test_service_auth_scopes.py).
# ---------------------------------------------------------------------------


def test_write_scope_rejects_mirror_only_key() -> None:
    with pytest.raises(HTTPException) as exc:
        require_write_scope(auth={"scopes": [SCOPE_MIRROR]})
    assert exc.value.status_code == 403


def test_write_scope_allows_write_key() -> None:
    auth = {"scopes": [SCOPE_WRITE]}
    assert require_write_scope(auth=auth) is auth


def test_mirror_scope_accepts_write_only_key() -> None:
    """A write-capable credential can safely also exercise the mirror route."""
    auth = {"scopes": [SCOPE_WRITE]}
    assert require_write_or_mirror_scope(auth=auth) is auth


def test_mirror_scope_accepts_mirror_only_key() -> None:
    auth = {"scopes": [SCOPE_MIRROR]}
    assert require_write_or_mirror_scope(auth=auth) is auth


def test_mirror_scope_rejects_unrelated_scope() -> None:
    with pytest.raises(HTTPException) as exc:
        require_write_or_mirror_scope(auth={"scopes": ["sub:inventory:read"]})
    assert exc.value.status_code == 403


def test_require_service_auth_rejects_missing_key(db_session) -> None:
    """No matching ApiKey row -> 401, confirmed against the real function."""
    with pytest.raises(HTTPException) as exc:
        require_service_auth(x_api_key="not-a-real-key", db=db_session)
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Route-level behavior (real router, real service, real db_session; auth is
# overridden the same way tests/integration/test_sub_operational_sync_v2.py
# overrides it for the equivalent Sub-authenticated route).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _kernel_idempotency_tables(db_session):
    """Create the kernel's at-most-once ledger tables on this file's SQLite
    engine. This is the first ERP caller of ``dotmac_kernel.idempotency``, so
    no existing fixture provisions them for the unit-test engine (they exist
    for real via ``alembic/versions/20260820_idempotency_ledger.py``)."""
    from dotmac_kernel.idempotency_models import (
        IdempotencyRecord as KernelIdempotencyRecord,
    )
    from dotmac_kernel.idempotency_models import PlatformIdempotencyRecord

    engine = db_session.get_bind()
    KernelIdempotencyRecord.__table__.create(engine, checkfirst=True)
    PlatformIdempotencyRecord.__table__.create(engine, checkfirst=True)
    yield


def _client(db_session: Session, *, scopes: list[str]) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[require_service_auth] = lambda: {
        "organization_id": ORG_ID,
        "scopes": scopes,
    }

    def override_db() -> Generator[Session, None, None]:
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db_with_service_org] = override_db
    return TestClient(app)


def _payload(**overrides) -> dict:
    base = {
        "source_invoice_id": str(uuid4()),
        "source_updated_at": "2026-09-17T10:00:00+00:00",
        "source_kind": "native",
        "disposition": "ready",
        "projection_fingerprint": VALID_FINGERPRINT,
        "digest_version": 1,
        "issues": [],
        "idempotency_key": "key-1",
    }
    base.update(overrides)
    return base


def _write(client: TestClient, payload: dict, capability_binding_id: str = "cap-1"):
    return client.post(
        f"/api/v1/integration/observations/{capability_binding_id}", json=payload
    )


def _mirror(client: TestClient, payload: dict, capability_binding_id: str = "cap-1"):
    return client.post(
        f"/api/v1/integration/observations/{capability_binding_id}/mirror",
        json=payload,
    )


def test_write_route_happy_path(db_session) -> None:
    client = _client(db_session, scopes=[SCOPE_WRITE])
    response = _write(client, _payload(idempotency_key="happy-1"))
    assert response.status_code == 200
    body = response.json()
    assert body["occurrence_count"] == 1
    assert body["replayed"] is False
    assert body["resolved_prior_count"] == 0
    UUID(body["outcome_id"])  # well-formed


def test_write_route_exact_duplicate_replays_without_rerunning_the_effect(
    db_session,
) -> None:
    client = _client(db_session, scopes=[SCOPE_WRITE])
    payload = _payload(idempotency_key="replay-1")

    with patch(
        "app.services.dotmac_sub.integrator_observations.record_invoice_sync_outcome",
        wraps=_real_record_invoice_sync_outcome,
    ) as spy:
        first = _write(client, payload)
        second = _write(client, payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert spy.call_count == 1


def test_write_route_same_key_different_payload_conflicts(db_session) -> None:
    client = _client(db_session, scopes=[SCOPE_WRITE])
    _write(client, _payload(idempotency_key="conflict-1"))
    response = _write(
        client,
        _payload(idempotency_key="conflict-1", source_invoice_id=str(uuid4())),
    )
    assert response.status_code == 409


def test_write_route_rejects_oversized_idempotency_key(db_session) -> None:
    client = _client(db_session, scopes=[SCOPE_WRITE])
    response = _write(client, _payload(idempotency_key="k" * 201))
    assert response.status_code == 422


def test_write_route_rejects_invalid_enum(db_session) -> None:
    client = _client(db_session, scopes=[SCOPE_WRITE])
    response = _write(client, _payload(disposition="not_a_real_disposition"))
    assert response.status_code == 422


def test_write_route_rejects_malformed_fingerprint(db_session) -> None:
    client = _client(db_session, scopes=[SCOPE_WRITE])
    response = _write(client, _payload(projection_fingerprint="not-hex"))
    assert response.status_code == 422


@pytest.mark.parametrize(
    "malformed",
    [
        "a" * 63,
        "A" * 64,
        "z" * 64,
    ],
)
def test_write_route_rejects_various_malformed_fingerprints(
    db_session, malformed: str
) -> None:
    client = _client(db_session, scopes=[SCOPE_WRITE])
    response = _write(client, _payload(projection_fingerprint=malformed))
    assert response.status_code == 422


def test_write_route_accepts_valid_digest_version_and_fingerprint(db_session) -> None:
    client = _client(db_session, scopes=[SCOPE_WRITE])
    response = _write(
        client,
        _payload(
            idempotency_key="valid-digest-1",
            digest_version=1,
            projection_fingerprint=VALID_FINGERPRINT,
        ),
    )
    assert response.status_code == 200


def test_write_route_rejects_unsupported_digest_version(db_session) -> None:
    client = _client(db_session, scopes=[SCOPE_WRITE])
    response = _write(client, _payload(digest_version=2))
    assert response.status_code == 422
    detail = str(response.json())
    assert "digest_version" in detail


def test_write_route_rejects_string_digest_version(db_session) -> None:
    """Strict typing: ``"1"`` must not be silently coerced to the int 1."""
    client = _client(db_session, scopes=[SCOPE_WRITE])
    response = _write(client, _payload(digest_version="1"))
    assert response.status_code == 422


def test_write_route_rejects_boolean_digest_version(db_session) -> None:
    """Strict typing: ``True`` must not be silently coerced to the int 1."""
    client = _client(db_session, scopes=[SCOPE_WRITE])
    response = _write(client, _payload(digest_version=True))
    assert response.status_code == 422


def test_write_route_rejects_blocked_without_issues(db_session) -> None:
    client = _client(db_session, scopes=[SCOPE_WRITE])
    response = _write(client, _payload(disposition="blocked", issues=[]))
    assert response.status_code == 422


def test_write_route_rejects_wrong_scope(db_session) -> None:
    client = _client(db_session, scopes=[SCOPE_MIRROR])
    response = _write(client, _payload(idempotency_key="wrong-scope-1"))
    assert response.status_code == 403


def test_mirror_route_happy_path_writes_nothing(db_session) -> None:
    client = _client(db_session, scopes=[SCOPE_MIRROR])
    response = _mirror(client, _payload(idempotency_key="mirror-1"))
    assert response.status_code == 200
    assert response.json() == {"validated": True}
    assert (
        db_session.scalar(select(DotmacSubInvoiceSyncOutcome.outcome_id).limit(1))
        is None
    )


def test_mirror_route_accepts_write_only_key(db_session) -> None:
    """Write implies mirror — a write-capable credential may also mirror."""
    client = _client(db_session, scopes=[SCOPE_WRITE])
    response = _mirror(client, _payload(idempotency_key="mirror-write-1"))
    assert response.status_code == 200
    assert response.json() == {"validated": True}


def test_mirror_route_rejects_invalid_payload_and_writes_nothing(db_session) -> None:
    client = _client(db_session, scopes=[SCOPE_MIRROR])
    response = _mirror(
        client, _payload(disposition="blocked", issues=[], idempotency_key="mirror-2")
    )
    assert response.status_code == 422
    assert (
        db_session.scalar(select(DotmacSubInvoiceSyncOutcome.outcome_id).limit(1))
        is None
    )


# ---------------------------------------------------------------------------
# Route mounting — same shape as tests/test_webhook_routes_mounted.py, which
# is read-only for this slice; this is a new, independent assertion.
# ---------------------------------------------------------------------------


def test_integrator_observation_routes_are_mounted() -> None:
    from app.main import app as full_app

    paths = {getattr(route, "path", "") for route in full_app.routes}
    for expected in (
        "/integration/observations/{capability_binding_id}",
        "/integration/observations/{capability_binding_id}/mirror",
    ):
        assert any(p == expected or p.endswith(expected) for p in paths), (
            f"integrator observation route {expected} is not mounted on the app"
        )
