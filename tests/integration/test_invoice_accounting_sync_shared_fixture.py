"""One real, shared invoice fixture through BOTH of ERP's consumption paths.

## Provenance (read this before touching the fixture file)

``tests/fixtures/shared_invoice_accounting_sync_v2_sample.json`` is copied
BYTE-FOR-BYTE from a dedicated ``dotmac_sub`` worktree, branch
``feat/invoice-accounting-sync-canonical-digest``, commit ``d67d9d968``, path
``tests/fixtures/invoice_accounting_sync_v2_sample.json`` (SHA-256
``c355f2b494f288947bc27e64dbd69b14428f1efdf0fd60d0d1e12729bf7bfc26``). The
fixture's ``projection_digest``
(``0dfecf2e1f96a2d1a63eb8e5e9f78f0aefbf661ab2c424b841b5bb2eda85aa7c``) is
Sub's own independently-verified oracle value for this exact invoice
projection — this test never recomputes it, only forwards it.

There is no shared build/dependency graph across ``dotmac_sub``, the
``dotmac_connector_sub_accounting`` connector, Integrator, and this ERP
repository. Synchronization is manual: the Sub source fixture and the
connector/core-generated wire fixture are plain copies with provenance, not
automated cross-repo CI. Each repository's own test suite, anchored to the
SAME fixture content, is the current enforcement mechanism.

## The two real entrypoints this fixture drives

1. **The shadow task's direct-pull path** — ``DotmacSubClient
   ._parse_invoice_accounting_sync_v2`` parses the raw fixture dict into a
   real ``InvoiceAccountingSyncRecord`` (never hand-built), then
   ``app.services.dotmac_sub.invoice_sync_shadow._command`` builds the real
   ``RecordInvoiceSyncOutcome``, persisted via
   ``app.services.dotmac_sub.invoice_sync_outcomes.record_invoice_sync_outcome``.
   This mirrors the existing ``_record()`` helper precedent in
   ``tests/services/test_dotmac_sub_invoice_sync_shadow.py``.
2. **The Integrator-delivered receiver path** — the normal request body is
   the exact golden wire document asserted by the connector's real
   ``map_item`` and the integration module's
   ``product_observation_document``. Integrator's own port test sends that
   document through its real HTTP client, including the idempotency header.
   This test drives the document through ERP's REAL mounted API route
   (``app.api.integrator_observations.router``) with service-auth/DB
   dependency overrides — exercising ``execute_once`` end-to-end, not just
   the service function.

## Environment disclosure

This suite requires a real, migrated PostgreSQL database
(``tests/integration/conftest.py``'s ``engine``/``db`` fixtures). Per this
repository's standing rule (tests run only on Git-hosted CI, never on a
workstation), these tests are authored but UNEXECUTED here — every expected
value below was derived by hand-tracing the fixture's data through the real
parsing/mapping/persistence code read during authoring, not by running
pytest.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.integrator_observations import SCOPE_WRITE, router as integrator_router
from app.api.service_principal import get_db_with_service_org, require_service_auth
from app.config import settings
from app.models.finance.ar.dotmac_sub_invoice_sync_outcome import (
    DotmacSubInvoiceSyncIssue,
    DotmacSubInvoiceSyncOutcome,
)
from app.models.finance.ar.dotmac_sub_invoice_sync_outcome_legacy import (
    DotmacSubInvoiceSyncOutcomeLegacy,
)
from app.models.finance.core_org.organization import Organization
from app.schemas.integrator_observation import INVOICE_ACCOUNTING_SYNC_CAPABILITY
from app.services.dotmac_sub.client import DotmacSubClient, DotmacSubConfig
from app.services.dotmac_sub.invoice_sync_outcomes import (
    InvoiceSyncRevisionConflict,
    record_invoice_sync_outcome,
)
from app.services.dotmac_sub.invoice_sync_shadow import _command as _shadow_command

pytestmark = pytest.mark.integration

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "shared_invoice_accounting_sync_v2_sample.json"
)
WIRE_FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "shared_invoice_product_observation_v1.json"
)

# Fixed test identity — organization is explicitly seeded at this exact id so
# both consumption paths observe the SAME invoice identity/revision key.
ORG_ID: UUID = UUID("aaaaaaaa-1111-4111-8111-111111111111")
BINDING_ID: UUID = UUID("bbbbbbbb-2222-4222-8222-222222222222")
INSTALLATION_ID: UUID = UUID("cccccccc-3333-4333-8333-333333333333")


def _load_fixture() -> dict[str, Any]:
    import json

    return cast(dict[str, Any], json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))


def _load_wire_fixture() -> dict[str, Any]:
    """The real connector + generic builder's golden document.

    Copied byte-for-byte from dotmac_starter_mt's
    ``dotmac-connector-sub-accounting/tests/fixtures`` on the companion
    ``feat/sub-erp-wire-contract-tests`` branch. That branch asserts the real
    ``map_item`` and ``product_observation_document`` produce this document.
    SHA-256: dec305d41b87d34563198faf4fd110f5884da3314ca8afa33770d014262ec038.
    """
    import json
    import hashlib

    assert hashlib.sha256(WIRE_FIXTURE_PATH.read_bytes()).hexdigest() == (
        "dec305d41b87d34563198faf4fd110f5884da3314ca8afa33770d014262ec038"
    )

    return cast(
        dict[str, Any], json.loads(WIRE_FIXTURE_PATH.read_text(encoding="utf-8"))
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _bound_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings, "integrator_invoice_sync_binding_id", str(BINDING_ID), raising=False
    )
    monkeypatch.setattr(
        settings, "integrator_invoice_sync_scope_kind", "organization", raising=False
    )
    monkeypatch.setattr(
        settings,
        "integrator_invoice_sync_scope_ref",
        "shared-fixture-org",
        raising=False,
    )
    monkeypatch.setattr(
        settings,
        "integrator_invoice_sync_installation_id",
        str(INSTALLATION_ID),
        raising=False,
    )


@pytest.fixture()
def seeded_organization(db: Session) -> Organization:
    """Seed the fixed-UUID ``Organization`` row both entrypoints resolve
    ``organization_id`` against. Explicit ``organization_id`` overrides the
    model's Python-side ``uuid.uuid4`` default."""
    org = Organization(
        organization_id=ORG_ID,
        organization_code=f"FIX-{uuid.uuid4().hex[:8].upper()}",
        legal_name="Shared Fixture Test Organization",
        functional_currency_code="NGN",
        presentation_currency_code="NGN",
        fiscal_year_end_month=12,
        fiscal_year_end_day=31,
        is_active=True,
    )
    db.add(org)
    db.flush()
    return org


def _receiver_client(db: Session) -> TestClient:
    app = FastAPI()
    app.include_router(integrator_router, prefix="/api/v1")
    app.dependency_overrides[require_service_auth] = lambda: {
        "organization_id": ORG_ID,
        "scopes": [SCOPE_WRITE],
    }

    def override_db() -> Generator[Session, None, None]:
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise

    app.dependency_overrides[get_db_with_service_org] = override_db
    return TestClient(app, raise_server_exceptions=False)


def _row_count(db: Session) -> int:
    return int(
        db.scalar(select(func.count()).select_from(DotmacSubInvoiceSyncOutcome)) or 0
    )


def _fetch_outcome(db: Session) -> DotmacSubInvoiceSyncOutcome:
    outcome = db.scalar(
        select(DotmacSubInvoiceSyncOutcome).where(
            DotmacSubInvoiceSyncOutcome.organization_id == ORG_ID
        )
    )
    assert outcome is not None, "expected a canonical outcome row to exist"
    return outcome


def _fetch_issues(db: Session, outcome_id: UUID) -> list[DotmacSubInvoiceSyncIssue]:
    return list(
        db.scalars(
            select(DotmacSubInvoiceSyncIssue)
            .where(DotmacSubInvoiceSyncIssue.outcome_id == outcome_id)
            .order_by(DotmacSubInvoiceSyncIssue.issue_code)
        )
    )


# ---------------------------------------------------------------------------
# Shadow-path helper — reuses/extends the `_record()` pattern from
# tests/services/test_dotmac_sub_invoice_sync_shadow.py, fed the SHARED
# FIXTURE's real data instead of synthetic field values.
# ---------------------------------------------------------------------------


def _dotmac_sub_client() -> DotmacSubClient:
    return DotmacSubClient(DotmacSubConfig(api_url="https://sub.test", api_token="t"))


def _parsed_command(fixture: dict[str, Any]):
    """Real parse -> real command, never a hand-built dataclass, per the
    ``_record()`` precedent in ``tests/services/test_dotmac_sub_invoice_sync_shadow.py``."""
    record = _dotmac_sub_client()._parse_invoice_accounting_sync_v2(fixture)
    return _shadow_command(ORG_ID, record)


def _run_shadow_path(db: Session, fixture: dict[str, Any]) -> None:
    """Record ``fixture`` via the shadow path. Left to raise on conflict —
    the unit under test for the shadow-path conflict case is this call
    itself, never anything downstream."""
    record_invoice_sync_outcome(db, _parsed_command(fixture))
    db.flush()


# ---------------------------------------------------------------------------
# Receiver-path helper — normal delivery uses the connector/core golden wire;
# these builders are retained for intentional mutation/conflict cases.
# ---------------------------------------------------------------------------


def _mapped_issues(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    mapped: list[dict[str, Any]] = []
    for issue in fixture["issues"]:
        item: dict[str, Any] = {"code": issue["code"]}
        if issue.get("line_id") is not None:
            item["source_line_id"] = issue["line_id"]
        if issue.get("expected_amount") is not None:
            item["expected_amount"] = issue["expected_amount"]
        if issue.get("actual_amount") is not None:
            item["actual_amount"] = issue["actual_amount"]
        mapped.append(item)
    return mapped


def _observation(fixture: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    observation = {
        "capability_id": INVOICE_ACCOUNTING_SYNC_CAPABILITY,
        "contract_version": fixture["contract_version"],
        "source_invoice_id": fixture["source_invoice_id"],
        "source_account_id": fixture["account_id"],
        "source_updated_at": fixture["updated_at"],
        "source_kind": fixture["source_kind"],
        "disposition": fixture["disposition"],
        "source_total_amount": fixture["total"],
        "source_currency": fixture["currency"],
        "source_issued_at": fixture["issued_at"],
        "source_due_at": fixture["due_at"],
        "digest_version": fixture["digest_version"],
        "projection_digest": fixture["projection_digest"],
        "issues": _mapped_issues(fixture),
    }
    observation.update(overrides)
    return observation


def _envelope(
    fixture: dict[str, Any], *, observation: dict[str, Any] | None = None
) -> dict[str, Any]:
    envelope = _load_wire_fixture()
    # Normal delivery uses the exact document emitted by the real connector
    # and generic builder. Only mutation tests replace its typed inner body.
    if observation is not None or fixture != _load_fixture():
        envelope["observation"] = (
            observation if observation is not None else _observation(fixture)
        )
    return envelope


def test_wire_fixture_matches_sub_source_and_erp_binding() -> None:
    fixture = _load_fixture()
    envelope = _load_wire_fixture()
    assert envelope["observation"] == _observation(fixture)
    assert envelope["capability_id"] == INVOICE_ACCOUNTING_SYNC_CAPABILITY
    assert envelope["source"]["installation_id"] == str(INSTALLATION_ID)
    assert envelope["scope"] == {"kind": "organization", "ref": "shared-fixture-org"}


def _run_receiver_path(
    client: TestClient, fixture: dict[str, Any], *, idempotency_key: str
):
    return client.post(
        f"/api/v1/integration/observations/{BINDING_ID}",
        json=_envelope(fixture),
        headers={"Idempotency-Key": idempotency_key},
    )


# ---------------------------------------------------------------------------
# 1. Both arrival orders produce the same stable, non-conflicting canonical
#    state — the single most important pair of tests in this task.
# ---------------------------------------------------------------------------


def test_shadow_first_then_receiver_is_a_stable_replay(
    db: Session, seeded_organization: Organization
) -> None:
    fixture = _load_fixture()

    _run_shadow_path(db, fixture)
    assert _row_count(db) == 1
    first_outcome = _fetch_outcome(db)
    first_issues = _fetch_issues(db, first_outcome.outcome_id)

    receiver_client = _receiver_client(db)
    response = _run_receiver_path(
        receiver_client, fixture, idempotency_key="shadow-first-receiver-replay"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["replayed"] is True
    assert body["outcome"] == "replayed"
    assert body["observation_id"] == str(first_outcome.outcome_id)
    assert _row_count(db) == 1  # no second canonical row was created

    second_outcome = _fetch_outcome(db)
    assert second_outcome.outcome_id == first_outcome.outcome_id
    assert second_outcome.occurrence_count == 2
    # Every canonical column identical except replay-only bookkeeping
    # (`occurrence_count`/`last_seen_at`).
    assert second_outcome.contract_version == first_outcome.contract_version
    assert second_outcome.source_kind == first_outcome.source_kind
    assert second_outcome.disposition == first_outcome.disposition
    assert second_outcome.projection_fingerprint == first_outcome.projection_fingerprint
    assert second_outcome.digest_version == first_outcome.digest_version
    assert second_outcome.issue_count == first_outcome.issue_count
    assert second_outcome.first_seen_at == first_outcome.first_seen_at

    second_issues = _fetch_issues(db, second_outcome.outcome_id)
    assert [
        (i.issue_code, i.source_line_id, i.expected_amount, i.actual_amount)
        for i in second_issues
    ] == [
        (i.issue_code, i.source_line_id, i.expected_amount, i.actual_amount)
        for i in first_issues
    ]


def test_receiver_first_then_shadow_is_a_stable_replay(
    db: Session, seeded_organization: Organization
) -> None:
    fixture = _load_fixture()
    receiver_client = _receiver_client(db)

    first_response = _run_receiver_path(
        receiver_client, fixture, idempotency_key="receiver-first-key"
    )
    assert first_response.status_code == 200
    assert _row_count(db) == 1
    first_outcome = _fetch_outcome(db)
    first_issues = _fetch_issues(db, first_outcome.outcome_id)

    _run_shadow_path(db, fixture)

    assert _row_count(db) == 1  # stable replay, not a second row
    second_outcome = _fetch_outcome(db)
    assert second_outcome.outcome_id == first_outcome.outcome_id
    assert second_outcome.occurrence_count == 2
    assert second_outcome.contract_version == first_outcome.contract_version
    assert second_outcome.source_kind == first_outcome.source_kind
    assert second_outcome.disposition == first_outcome.disposition
    assert second_outcome.projection_fingerprint == first_outcome.projection_fingerprint
    assert second_outcome.digest_version == first_outcome.digest_version
    assert second_outcome.issue_count == first_outcome.issue_count
    assert second_outcome.first_seen_at == first_outcome.first_seen_at

    second_issues = _fetch_issues(db, second_outcome.outcome_id)
    assert [
        (i.issue_code, i.source_line_id, i.expected_amount, i.actual_amount)
        for i in second_issues
    ] == [
        (i.issue_code, i.source_line_id, i.expected_amount, i.actual_amount)
        for i in first_issues
    ]


# ---------------------------------------------------------------------------
# 2. Legacy-row bridging at full integration level, both entrypoints.
# ---------------------------------------------------------------------------


def _seed_legacy_row(
    db: Session, fixture: dict[str, Any]
) -> DotmacSubInvoiceSyncOutcomeLegacy:
    parsed = _dotmac_sub_client()._parse_invoice_accounting_sync_v2(fixture)
    legacy = DotmacSubInvoiceSyncOutcomeLegacy(
        organization_id=ORG_ID,
        source_invoice_id=UUID(fixture["source_invoice_id"]),
        source_updated_at=parsed.updated_at,
        contract_version=fixture["contract_version"],
        source_kind=fixture["source_kind"],
        disposition="ready",
        projection_fingerprint="f" * 64,  # distinct legacy-style fingerprint
        issue_count=0,
        occurrence_count=1,
    )
    db.add(legacy)
    db.flush()
    return legacy


def _snapshot_legacy(row: DotmacSubInvoiceSyncOutcomeLegacy) -> dict[str, Any]:
    return {
        "outcome_id": row.outcome_id,
        "organization_id": row.organization_id,
        "source_invoice_id": row.source_invoice_id,
        "source_updated_at": row.source_updated_at,
        "contract_version": row.contract_version,
        "source_kind": row.source_kind,
        "disposition": row.disposition,
        "projection_fingerprint": row.projection_fingerprint,
        "issue_count": row.issue_count,
        "occurrence_count": row.occurrence_count,
        "resolved_at": row.resolved_at,
    }


def test_legacy_row_does_not_block_shadow_path_and_stays_untouched(
    db: Session, seeded_organization: Organization
) -> None:
    fixture = _load_fixture()
    legacy = _seed_legacy_row(db, fixture)
    before = _snapshot_legacy(legacy)

    _run_shadow_path(db, fixture)

    assert _row_count(db) == 1  # fresh canonical row, not blocked/conflicted
    canonical = _fetch_outcome(db)
    assert canonical.projection_fingerprint == fixture["projection_digest"]

    db.flush()
    refetched = db.get(DotmacSubInvoiceSyncOutcomeLegacy, legacy.outcome_id)
    assert refetched is not None
    assert _snapshot_legacy(refetched) == before


def test_legacy_row_does_not_block_receiver_path_and_stays_untouched(
    db: Session, seeded_organization: Organization
) -> None:
    fixture = _load_fixture()
    legacy = _seed_legacy_row(db, fixture)
    before = _snapshot_legacy(legacy)

    receiver_client = _receiver_client(db)
    response = _run_receiver_path(
        receiver_client, fixture, idempotency_key="legacy-bridging-receiver"
    )

    assert response.status_code == 200
    assert response.json()["replayed"] is False
    assert _row_count(db) == 1
    canonical = _fetch_outcome(db)
    assert canonical.projection_fingerprint == fixture["projection_digest"]

    db.flush()
    refetched = db.get(DotmacSubInvoiceSyncOutcomeLegacy, legacy.outcome_id)
    assert refetched is not None
    assert _snapshot_legacy(refetched) == before


# ---------------------------------------------------------------------------
# 3/4. Same-revision CONTENT conflict, through the real entrypoints.
# ---------------------------------------------------------------------------

_ALTERED_DIGEST = "1" * 64  # a different, valid lowercase-64-hex value


def _altered_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    altered = dict(fixture)
    altered["projection_digest"] = _ALTERED_DIGEST
    return altered


def test_receiver_path_same_revision_conflict_is_typed_409(
    db: Session, seeded_organization: Organization
) -> None:
    fixture = _load_fixture()
    _run_shadow_path(db, fixture)
    assert _row_count(db) == 1

    receiver_client = _receiver_client(db)
    response = _run_receiver_path(
        receiver_client,
        _altered_fixture(fixture),
        idempotency_key="revision-conflict-receiver",
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "invoices.accounting_sync.identity_collision"
    # The conflicting write must never have landed as a second canonical row.
    assert _row_count(db) == 1


def test_shadow_path_same_revision_conflict_raises_revision_conflict(
    db: Session, seeded_organization: Organization
) -> None:
    fixture = _load_fixture()
    receiver_client = _receiver_client(db)
    response = _run_receiver_path(
        receiver_client, fixture, idempotency_key="revision-conflict-shadow-setup"
    )
    assert response.status_code == 200
    assert _row_count(db) == 1

    with pytest.raises(InvoiceSyncRevisionConflict):
        record_invoice_sync_outcome(db, _parsed_command(_altered_fixture(fixture)))

    assert _row_count(db) == 1  # the conflicting write never landed
