"""Integrator ProductPort receiver for Sub's invoice-accounting-sync observations.

Route-level tests against the generic v2 envelope (see
``app/schemas/integrator_observation.py``). Auth is overridden the same way
``tests/integration/test_sub_operational_sync_v2.py`` overrides it for the
equivalent Sub-authenticated route.

``tests/conftest.py`` replaces ``sys.modules["app.config"]`` wholesale with a
plain, mutable ``MockSettings`` class (not the real frozen dataclass) that
does not carry the three new ``integrator_invoice_sync_*`` fields this task
adds — this file's ``_bound_settings`` fixture patches them on, following the
same ``monkeypatch.setattr(settings, ..., raising=False)`` convention already
used by ``tests/services/test_dotmac_sub_webhook_binding_seed.py``.

The write route uses ERP's existing endpoint-response idempotency owner. The
platform adoption ledger explicitly defers the dotmac-kernel runtime cutover.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Generator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.integrator_observations import (
    SCOPE_MIRROR,
    SCOPE_WRITE,
    require_write_or_mirror_scope,
    require_write_scope,
    router,
)
from app.api.service_principal import get_db_with_service_org, require_service_auth
from app.config import settings
from app.models.finance.ar.dotmac_sub_invoice_sync_outcome import (
    DotmacSubInvoiceSyncOutcome,
)
from app.services.dotmac_sub.integrator_observations import (
    invoice_accounting_sync_product_port_descriptor,
)

ORG_ID = uuid4()
TEST_BINDING_ID = uuid4()
INSTALLATION_ID = uuid4()
VALID_DIGEST = "a" * 64
CAPABILITY_ID = "invoices.accounting_sync.observation.v1"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _bound_settings(monkeypatch):
    """Patch the three new settings knobs onto the shared test MockSettings."""
    monkeypatch.setattr(
        settings,
        "integrator_invoice_sync_binding_id",
        str(TEST_BINDING_ID),
        raising=False,
    )
    monkeypatch.setattr(
        settings, "integrator_invoice_sync_scope_kind", "organization", raising=False
    )
    monkeypatch.setattr(
        settings, "integrator_invoice_sync_scope_ref", "org-under-test", raising=False
    )
    monkeypatch.setattr(
        settings,
        "integrator_invoice_sync_installation_id",
        str(INSTALLATION_ID),
        raising=False,
    )


def _client(
    db_session: Session, *, scopes: list[str], install_global_handlers: bool = False
) -> TestClient:
    app = FastAPI()
    if install_global_handlers:
        # Prove the router-local override — not the ABSENCE of a global
        # handler — is what preserves `detail`: install this app's REAL
        # global exception handlers before mounting the router.
        from app.errors import register_error_handlers

        register_error_handlers(app)
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
    return TestClient(app, raise_server_exceptions=False)


def _observation(**overrides) -> dict:
    source_invoice_id = overrides.pop("source_invoice_id", str(uuid4()))
    source_updated_at = overrides.pop("source_updated_at", "2026-09-17T10:00:00+00:00")
    base = {
        "capability_id": CAPABILITY_ID,
        "contract_version": "invoice-accounting-sync.v2",
        "source_invoice_id": source_invoice_id,
        "source_account_id": str(uuid4()),
        "source_updated_at": source_updated_at,
        "source_kind": "native",
        "disposition": "ready",
        "source_total_amount": "100.00",
        "source_currency": "NGN",
        "source_issued_at": "2026-09-16T10:00:00+00:00",
        "source_due_at": "2026-09-30T10:00:00+00:00",
        "digest_version": 1,
        "projection_digest": VALID_DIGEST,
        "issues": [],
    }
    base.update(overrides)
    return base


def _envelope(*, observation: dict | None = None, **overrides) -> dict:
    observation = observation if observation is not None else _observation()
    envelope = {
        "schema_version": "dotmac.io/product-observation/v1",
        "capability_id": CAPABILITY_ID,
        "contract_version": 1,
        "source": {
            "installation_id": str(INSTALLATION_ID),
            "connector_key": "sub_accounting",
        },
        "provider_event_id": (
            f"sub_accounting:{observation.get('source_invoice_id', 'missing')}:"
            f"{observation.get('source_updated_at', 'missing')}"
        ),
        "event_type": CAPABILITY_ID,
        "scope": {"kind": "organization", "ref": "org-under-test"},
        "observation": observation,
    }
    envelope.update(overrides)
    return envelope


def _write(
    client: TestClient,
    envelope: dict,
    *,
    idempotency_key: str = "key-1",
    correlation_id: str | None = None,
    binding_id: UUID = TEST_BINDING_ID,
):
    headers = {"Idempotency-Key": idempotency_key}
    if correlation_id is not None:
        headers["X-Correlation-Id"] = correlation_id
    return client.post(
        f"/api/v1/integration/observations/{binding_id}",
        json=envelope,
        headers=headers,
    )


def _mirror(client: TestClient, envelope: dict, *, binding_id: UUID = TEST_BINDING_ID):
    return client.post(
        f"/api/v1/integration/observations/{binding_id}/mirror",
        json=envelope,
    )


def _descriptor(client: TestClient, *, binding_id: UUID = TEST_BINDING_ID):
    return client.get(f"/api/v1/integration/observations/{binding_id}/descriptor")


def _row_count(db_session: Session) -> int:
    return int(
        db_session.scalar(select(func.count()).select_from(DotmacSubInvoiceSyncOutcome))
        or 0
    )


# ---------------------------------------------------------------------------
# Scope-dependency behavior — direct calls (unchanged shape from slice 1c).
# ---------------------------------------------------------------------------


def test_write_scope_rejects_mirror_only_key() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        require_write_scope(auth={"scopes": [SCOPE_MIRROR]})
    assert exc.value.status_code == 403


def test_mirror_scope_accepts_write_only_key() -> None:
    auth = {"scopes": [SCOPE_WRITE]}
    assert require_write_or_mirror_scope(auth=auth) is auth


# ---------------------------------------------------------------------------
# THE single most important test: HTTP-level replay must win over the
# domain's own first-ever stored `replayed=False`.
# ---------------------------------------------------------------------------


def test_write_route_http_replay_wins_over_domain_replayed_false(db_session) -> None:
    """Same Idempotency-Key presented twice must answer replayed=true on the
    SECOND call — even though the domain's own FIRST stored write recorded
    replayed=False for itself (this is genuinely its first-ever recording).
    Getting this wrong makes a retried delivery look like a brand-new
    ACCEPTED to the real client, silently hiding a double-send.
    """
    client = _client(db_session, scopes=[SCOPE_WRITE])
    envelope = _envelope()

    first = _write(client, envelope, idempotency_key="replay-key-1")
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["replayed"] is False  # domain: genuinely first-ever
    assert first_body["outcome"] == "recorded"

    second = _write(client, envelope, idempotency_key="replay-key-1")
    assert second.status_code == 200
    second_body = second.json()
    # The domain's stored replayed flag is still False in the ledger (that is
    # what was recorded the first time) — the HTTP-level replay is what
    # must flip this to true.
    assert second_body["replayed"] is True
    assert second_body["outcome"] == "replayed"
    # Everything else is byte-for-byte identical — a pure replay, no rerun.
    assert second_body["observation_id"] == first_body["observation_id"]
    assert second_body["occurrence_count"] == first_body["occurrence_count"] == 1


def test_write_route_domain_replay_alone_also_sets_true(db_session) -> None:
    """A DIFFERENT idempotency key hitting the SAME invoice revision produces
    a domain-level replay (occurrence_count increments) on the HTTP owner's
    FIRST execution of that key — the OR's other side."""
    client = _client(db_session, scopes=[SCOPE_WRITE])
    observation = _observation()
    envelope = _envelope(observation=observation)

    first = _write(client, envelope, idempotency_key="domain-replay-key-1")
    assert first.status_code == 200
    assert first.json()["replayed"] is False

    second = _write(client, envelope, idempotency_key="domain-replay-key-2")
    assert second.status_code == 200
    body = second.json()
    assert body["replayed"] is True
    assert body["occurrence_count"] == 2


# ---------------------------------------------------------------------------
# Router-local error override — proven against the app's REAL global handlers.
# ---------------------------------------------------------------------------


def test_malformed_envelope_returns_typed_object_detail_even_with_real_global_handlers(
    db_session,
) -> None:
    client = _client(db_session, scopes=[SCOPE_WRITE], install_global_handlers=True)
    envelope = _envelope(observation=_observation(capability_id="wrong.capability"))

    response = _write(client, envelope, idempotency_key="malformed-1")

    assert response.status_code == 422
    body = response.json()
    # The app's global RequestValidationError handler would have produced
    # {"code": "validation_error", "message": "Validation error",
    #  "details": [...]} with NO top-level `detail` key — this router's own
    # _TypedErrorRoute must have intercepted it instead.
    assert "detail" in body
    assert isinstance(body["detail"], dict)
    assert body["detail"]["code"] == "invoices.accounting_sync.schema_rejected"
    assert "capability_id" in body["detail"]["message"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda obs: {**obs, "capability_id": "wrong.capability"},
        lambda obs: {**obs, "projection_digest": "not-hex"},
        lambda obs: {**obs, "digest_version": 2},
        lambda obs: {k: v for k, v in obs.items() if k != "source_invoice_id"},
    ],
)
def test_write_route_rejects_malformed_envelope_with_typed_422(
    db_session, mutate
) -> None:
    client = _client(db_session, scopes=[SCOPE_WRITE])
    envelope = _envelope(observation=mutate(_observation()))

    response = _write(client, envelope, idempotency_key="malformed-parametrized")

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "invoices.accounting_sync.schema_rejected"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda obs: {**obs, "capability_id": "wrong.capability"},
        lambda obs: {**obs, "projection_digest": "not-hex"},
        lambda obs: {**obs, "digest_version": 2},
        lambda obs: {k: v for k, v in obs.items() if k != "source_invoice_id"},
    ],
)
def test_mirror_route_never_422s_on_the_same_malformed_bodies_the_write_route_rejects(
    db_session, mutate
) -> None:
    """The write-route counterparts of these cases correctly 422 (strict
    schema). The mirror route must instead answer 200/blocked for every one
    of them — this is precisely the class of disagreement a mirror pass
    exists to surface as evidence, not as a client-visible transport
    failure (F3)."""
    client = _client(db_session, scopes=[SCOPE_MIRROR])
    envelope = _envelope(observation=mutate(_observation()))

    response = _mirror(client, envelope)

    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "blocked"
    assert body["agrees"] is False
    assert body["blocking_reasons"]


def test_mirror_route_rejects_extra_field_as_blocked_not_422(db_session) -> None:
    client = _client(db_session, scopes=[SCOPE_MIRROR])
    observation = _observation()
    observation["unexpected_field"] = "nope"

    response = _mirror(client, _envelope(observation=observation))

    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "blocked"
    assert body["agrees"] is False


def test_mirror_route_malformed_json_body_is_blocked_not_500(db_session) -> None:
    client = _client(db_session, scopes=[SCOPE_MIRROR])

    response = client.post(
        f"/api/v1/integration/observations/{TEST_BINDING_ID}/mirror",
        content=b"{not-json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "blocked"
    assert body["agrees"] is False


def test_write_route_rejects_extra_field_on_observation(db_session) -> None:
    client = _client(db_session, scopes=[SCOPE_WRITE])
    observation = _observation()
    observation["unexpected_field"] = "nope"
    envelope = _envelope(observation=observation)

    response = _write(client, envelope, idempotency_key="extra-1")
    assert response.status_code == 422


def test_write_route_rejects_extra_field_on_envelope(db_session) -> None:
    client = _client(db_session, scopes=[SCOPE_WRITE])
    envelope = _envelope()
    envelope["unexpected_field"] = "nope"

    response = _write(client, envelope, idempotency_key="extra-2")
    assert response.status_code == 422


def test_write_route_accepts_omitted_optional_dates(db_session) -> None:
    client = _client(db_session, scopes=[SCOPE_WRITE])
    observation = _observation()
    del observation["source_issued_at"]
    del observation["source_due_at"]

    response = _write(
        client, _envelope(observation=observation), idempotency_key="omitted-dates"
    )
    assert response.status_code == 200


def test_write_route_accepts_explicit_null_optional_dates(db_session) -> None:
    client = _client(db_session, scopes=[SCOPE_WRITE])
    observation = _observation(source_issued_at=None, source_due_at=None)

    response = _write(
        client, _envelope(observation=observation), idempotency_key="null-dates"
    )
    assert response.status_code == 200


def test_write_route_rejects_oversized_correlation_id_header(db_session) -> None:
    """F1: the real ``IdempotencyRecord.correlation_id`` column is
    ``String(200)`` (both the kernel model and this repo's own migration).
    An unbounded header would otherwise reach `db.flush()` and raise a
    Postgres ``DataError`` — not caught anywhere in this route, falling
    through to a 500 the real client misreads as retryable UNAVAILABLE and
    retries the same poison header forever. Bounding the header parameter
    turns this into an honest, terminal 422 before the DB is ever touched
    (verified here at the header-parameter level; SQLite itself would not
    enforce the column length)."""
    client = _client(db_session, scopes=[SCOPE_WRITE])

    response = client.post(
        f"/api/v1/integration/observations/{TEST_BINDING_ID}",
        json=_envelope(),
        headers={
            "Idempotency-Key": "correlation-oversized-1",
            "X-Correlation-Id": "x" * 201,
        },
    )

    assert response.status_code == 422


def test_write_route_rejects_whitespace_only_idempotency_key(db_session) -> None:
    """F2: ``min_length=1`` alone admits a single space. ``dotmac_kernel``'s
    own ``_validate`` would reject this with ``BadRequestError`` — a type
    this app has no registered handler for, which would otherwise 500 and
    be misread as retryable UNAVAILABLE, retrying the same poison key
    forever. The route must refuse it as an honest, terminal 422 itself."""
    client = _client(db_session, scopes=[SCOPE_WRITE])

    response = _write(client, _envelope(), idempotency_key="   ")

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "invoices.accounting_sync.schema_rejected"


# ---------------------------------------------------------------------------
# Identity collision -> 409 typed, distinguished from a plain 422.
# ---------------------------------------------------------------------------


def test_write_route_same_revision_key_different_content_is_typed_409(
    db_session,
) -> None:
    client = _client(db_session, scopes=[SCOPE_WRITE])
    source_invoice_id = str(uuid4())
    source_updated_at = "2026-09-17T10:00:00+00:00"

    first = _write(
        client,
        _envelope(
            observation=_observation(
                source_invoice_id=source_invoice_id,
                source_updated_at=source_updated_at,
                disposition="ready",
            )
        ),
        idempotency_key="collision-key-1",
    )
    assert first.status_code == 200

    second = _write(
        client,
        _envelope(
            observation=_observation(
                source_invoice_id=source_invoice_id,
                source_updated_at=source_updated_at,
                disposition="blocked",
                issues=[
                    {
                        "code": "no_active_lines",
                        "source_line_id": str(uuid4()),
                        "expected_amount": "1.00",
                        "actual_amount": "2.00",
                    }
                ],
            )
        ),
        idempotency_key="collision-key-2",
    )
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"].endswith("identity_collision")


def test_write_route_same_key_different_payload_is_idempotency_conflict_409(
    db_session,
) -> None:
    client = _client(db_session, scopes=[SCOPE_WRITE])
    _write(client, _envelope(), idempotency_key="idem-conflict-1")
    response = _write(
        client,
        _envelope(observation=_observation(source_invoice_id=str(uuid4()))),
        idempotency_key="idem-conflict-1",
    )
    assert response.status_code == 409
    # Confirm it's specifically the idempotency-conflict path (plain-string
    # detail) under test here, not InvoiceSyncIdentityCollision (typed-object
    # detail) — both answer 409, but the real client's behavior differs by
    # shape, and the two must not be conflated.
    assert isinstance(response.json()["detail"], str)


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "source": {
                "installation_id": str(uuid4()),
                "connector_key": "sub_accounting",
            }
        },
        {"source": {"installation_id": str(INSTALLATION_ID), "connector_key": "other"}},
        {"scope": {"kind": "organization", "ref": "another-org"}},
    ],
)
def test_write_route_rejects_unbound_provenance(db_session, overrides) -> None:
    client = _client(db_session, scopes=[SCOPE_WRITE])
    before = _row_count(db_session)
    response = _write(client, _envelope(**overrides), idempotency_key="bad-provenance")
    assert response.status_code == 422
    assert response.json()["detail"]["code"].endswith("schema_rejected")
    assert _row_count(db_session) == before


def test_mirror_reports_unbound_provenance_as_blocked_without_writing(
    db_session,
) -> None:
    client = _client(db_session, scopes=[SCOPE_MIRROR])
    before = _row_count(db_session)
    response = _mirror(
        client, _envelope(scope={"kind": "organization", "ref": "another-org"})
    )
    assert response.status_code == 200
    assert response.json()["verdict"] == "blocked"
    assert response.json()["agrees"] is False
    assert _row_count(db_session) == before


def test_same_key_changed_provider_event_identity_conflicts(db_session) -> None:
    client = _client(db_session, scopes=[SCOPE_WRITE])
    first = _envelope()
    assert (
        _write(client, first, idempotency_key="outer-identity-key").status_code == 200
    )
    changed = _envelope(provider_event_id="different-event")
    response = _write(client, changed, idempotency_key="outer-identity-key")
    assert response.status_code == 409
    assert isinstance(response.json()["detail"], str)


# ---------------------------------------------------------------------------
# Binding mismatch -> plain-string 404 on all three routes.
# ---------------------------------------------------------------------------


def test_wrong_binding_id_is_plain_string_404_on_write(db_session) -> None:
    client = _client(db_session, scopes=[SCOPE_WRITE])
    response = _write(
        client, _envelope(), binding_id=uuid4(), idempotency_key="wrong-1"
    )
    assert response.status_code == 404
    assert isinstance(response.json()["detail"], str)


def test_wrong_binding_id_is_plain_string_404_on_mirror(db_session) -> None:
    client = _client(db_session, scopes=[SCOPE_MIRROR])
    response = _mirror(client, _envelope(), binding_id=uuid4())
    assert response.status_code == 404
    assert isinstance(response.json()["detail"], str)


def test_wrong_binding_id_is_plain_string_404_on_descriptor(db_session) -> None:
    client = _client(db_session, scopes=[SCOPE_MIRROR])
    response = _descriptor(client, binding_id=uuid4())
    assert response.status_code == 404
    assert isinstance(response.json()["detail"], str)


# ---------------------------------------------------------------------------
# Mirror route: honest, read-only comparison.
# ---------------------------------------------------------------------------


def test_mirror_route_validation_failure_is_200_blocked_never_non_200(
    db_session,
) -> None:
    client = _client(db_session, scopes=[SCOPE_MIRROR])
    before = _row_count(db_session)
    envelope = _envelope(observation=_observation(digest_version=2))

    response = _mirror(client, envelope)

    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "blocked"
    assert body["agrees"] is False
    assert body["blocking_reasons"]
    assert _row_count(db_session) == before


def test_mirror_route_no_existing_row_is_honest_missing(db_session) -> None:
    client = _client(db_session, scopes=[SCOPE_MIRROR])
    before = _row_count(db_session)

    response = _mirror(client, _envelope())

    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "missing"
    assert body["agrees"] is False
    assert body["counterpart_identity"] is None
    assert _row_count(db_session) == before


def test_mirror_route_matching_existing_row_agrees(db_session) -> None:
    write_client = _client(db_session, scopes=[SCOPE_WRITE])
    mirror_client = _client(db_session, scopes=[SCOPE_MIRROR])
    envelope = _envelope()

    write_response = _write(write_client, envelope, idempotency_key="mirror-match-1")
    assert write_response.status_code == 200
    before = _row_count(db_session)

    response = _mirror(mirror_client, envelope)

    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "match"
    assert body["agrees"] is True
    assert body["counterpart_identity"] == write_response.json()["observation_id"]
    assert _row_count(db_session) == before  # mirror wrote nothing


def test_mirror_route_disagreeing_existing_row_is_blocked_with_named_fields(
    db_session,
) -> None:
    write_client = _client(db_session, scopes=[SCOPE_WRITE])
    mirror_client = _client(db_session, scopes=[SCOPE_MIRROR])
    source_invoice_id = str(uuid4())
    source_updated_at = "2026-09-17T10:00:00+00:00"

    written = _write(
        write_client,
        _envelope(
            observation=_observation(
                source_invoice_id=source_invoice_id,
                source_updated_at=source_updated_at,
                source_kind="native",
            )
        ),
        idempotency_key="mirror-disagree-1",
    )
    assert written.status_code == 200
    before = _row_count(db_session)

    response = _mirror(
        mirror_client,
        _envelope(
            observation=_observation(
                source_invoice_id=source_invoice_id,
                source_updated_at=source_updated_at,
                source_kind="splynx_legacy",
            )
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "blocked"
    assert body["agrees"] is False
    fields = {item["field"] for item in body["disagreements"]}
    assert "source_kind" in fields
    assert _row_count(db_session) == before  # mirror never writes


# ---------------------------------------------------------------------------
# Descriptor route.
# ---------------------------------------------------------------------------

_EXPECTED_DESCRIPTOR_FIELDS = {
    "schema_version",
    "wire_schema_version",
    "application",
    "owner_module",
    "capability_id",
    "capability_summary",
    "contract_version",
    "destination_binding_id",
    "delivery_path",
    "mirror_path",
    "destination_scope",
    "activation_state",
    "source_revision",
    "capability_contract",
    "descriptor_digest",
}


def test_descriptor_route_returns_exactly_the_v3_fields(db_session) -> None:
    client = _client(db_session, scopes=[SCOPE_MIRROR])

    response = _descriptor(client)

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == _EXPECTED_DESCRIPTOR_FIELDS
    assert body["schema_version"] == "dotmac.io/product-port-descriptor/v3"
    assert body["wire_schema_version"] == "dotmac.io/product-observation/v1"
    assert body["capability_contract"]["observation_schema"] is not None
    assert body["capability_contract"]["schema_grace"] is None
    assert body["capability_contract"]["contract_digest"] == (
        "f69ffd1e486a298bfdff5ab6a4b1a30a6962075b30d91400ef6ca4247609ac74"
    )
    assert body["application"] == "erp"
    assert body["capability_id"] == CAPABILITY_ID
    assert body["activation_state"] == "configured_disabled"
    assert body["delivery_path"] == (
        f"/api/v1/integration/observations/{TEST_BINDING_ID}"
    )
    assert body["mirror_path"] == body["delivery_path"] + "/mirror"


def test_descriptor_digest_is_independently_reproducible(db_session) -> None:
    client = _client(db_session, scopes=[SCOPE_MIRROR])
    body = _descriptor(client).json()

    published = {k: v for k, v in body.items() if k != "descriptor_digest"}
    recomputed = hashlib.sha256(
        json.dumps(
            published, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
    ).hexdigest()

    assert recomputed == body["descriptor_digest"]


def test_published_descriptor_matches_integrator_contract_fixture(
    db_session, monkeypatch
) -> None:
    """The copied v3 fixture used by Integrator must match ERP's live owner."""
    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "erp_invoice_product_port_descriptor_v3.json"
    )
    assert hashlib.sha256(fixture_path.read_bytes()).hexdigest() == (
        "98fedb2a7721dbb2c0b1b0f15554dfedfe35ee41bcd83db017ccdda7fe04cc49"
    )
    binding_id = UUID("bbbbbbbb-2222-4222-8222-222222222222")
    monkeypatch.setattr(
        settings, "integrator_invoice_sync_binding_id", str(binding_id), raising=False
    )
    monkeypatch.setattr(
        settings,
        "integrator_invoice_sync_scope_ref",
        "shared-fixture-org",
        raising=False,
    )
    published = invoice_accounting_sync_product_port_descriptor(binding_id)
    assert published.model_dump(mode="json") == json.loads(fixture_path.read_text())


# ---------------------------------------------------------------------------
# Route mounting.
# ---------------------------------------------------------------------------


def test_integrator_observation_routes_are_mounted() -> None:
    from app.main import app as full_app

    paths = {getattr(route, "path", "") for route in full_app.routes}
    for expected in (
        "/integration/observations/{capability_binding_id}",
        "/integration/observations/{capability_binding_id}/mirror",
        "/integration/observations/{capability_binding_id}/descriptor",
    ):
        assert any(p == expected or p.endswith(expected) for p in paths), (
            f"integrator observation route {expected} is not mounted on the app"
        )
