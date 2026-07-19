"""Webhook org attribution (audit D2): the verifying binding IS the identity.

Per-org IntegrationConfig(DOTMAC_SUB) rows are the single definition authority
for inbound-webhook org attribution; the env-secret + DEFAULT_ORGANIZATION_ID
path is a retiring legacy authority behind DOTMAC_SUB_WEBHOOK_ORG_RESOLUTION:

- ``legacy`` — old precedence (env secret → default org first, rows second).
- ``shadow`` (default) — legacy precedence still decides, but the binding
  resolution always runs and divergence is logged as cutover evidence.
- ``strict`` — bindings only; the env path never attributes; fail closed.

The binding must be injective (secret → at most one org): a secret shared by
two orgs is a configuration error and attribution is refused.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import dotmac_sub as ds


def _sig(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class _FakeDB:
    def __init__(self, configs):
        self._configs = configs

    def execute(self, stmt):
        configs = self._configs

        class _R:
            def scalars(self):
                class _S:
                    def all(self):
                        return configs

                return _S()

        return _R()


def _cfg(org, api_secret="enc:x"):  # noqa: S107 — test fixture, not a secret
    return SimpleNamespace(organization_id=org, api_secret=api_secret)


def _set(monkeypatch, *, mode=None, env_secret=None, default_org=None):
    if mode is not None:
        monkeypatch.setattr(
            ds.settings, "dotmac_sub_webhook_org_resolution", mode, raising=False
        )
    monkeypatch.setattr(
        ds.settings, "dotmac_sub_webhook_secret", env_secret, raising=False
    )
    monkeypatch.setattr(
        ds.settings, "default_organization_id", default_org, raising=False
    )


def _decrypt_map(monkeypatch, mapping):
    monkeypatch.setattr(
        "app.services.integration_config.decrypt_credential",
        lambda value, db=None: mapping.get(value),
    )


BODY = b'{"event_type":"x"}'


# ---------------------------------------------------------------------------
# legacy mode — old precedence, unchanged (escape hatch)
# ---------------------------------------------------------------------------


def test_legacy_env_secret_resolves_default_org(monkeypatch):
    org = uuid.uuid4()
    _set(monkeypatch, mode="legacy", env_secret="env-s", default_org=str(org))
    assert ds._resolve_webhook_org(_FakeDB([]), BODY, _sig(BODY, "env-s")) == org


def test_legacy_falls_through_to_binding_rows(monkeypatch):
    org = uuid.uuid4()
    _set(monkeypatch, mode="legacy", env_secret="env-s", default_org=None)
    _decrypt_map(monkeypatch, {"enc:x": "org-secret"})
    db = _FakeDB([_cfg(org)])
    assert ds._resolve_webhook_org(db, BODY, _sig(BODY, "org-secret")) == org


def test_legacy_nothing_verifies_returns_none(monkeypatch):
    _set(monkeypatch, mode="legacy", env_secret="env-s", default_org=str(uuid.uuid4()))
    _decrypt_map(monkeypatch, {"enc:x": "org-secret"})
    db = _FakeDB([_cfg(uuid.uuid4())])
    assert ds._resolve_webhook_org(db, BODY, _sig(BODY, "wrong")) is None


# ---------------------------------------------------------------------------
# strict mode — bindings only, fail closed
# ---------------------------------------------------------------------------


def test_strict_resolves_the_verifying_binding_among_several(monkeypatch):
    org_a, org_b, org_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    _set(monkeypatch, mode="strict", env_secret="env-s", default_org=str(org_a))
    _decrypt_map(
        monkeypatch, {"enc:a": "secret-a", "enc:b": "secret-b", "enc:c": "secret-c"}
    )
    db = _FakeDB([_cfg(org_a, "enc:a"), _cfg(org_b, "enc:b"), _cfg(org_c, "enc:c")])
    assert ds._resolve_webhook_org(db, BODY, _sig(BODY, "secret-b")) == org_b


def test_strict_rejects_env_secret_signed_delivery(monkeypatch):
    """Fail-closed pin: in strict mode the env path never attributes."""
    org = uuid.uuid4()
    _set(monkeypatch, mode="strict", env_secret="env-s", default_org=str(org))
    _decrypt_map(monkeypatch, {"enc:x": "org-secret"})
    db = _FakeDB([_cfg(uuid.uuid4())])
    assert ds._resolve_webhook_org(db, BODY, _sig(BODY, "env-s")) is None


def test_strict_ambiguous_binding_fails_closed(monkeypatch, caplog):
    """Injectivity guard: a secret shared by two orgs refuses attribution."""
    org_a, org_b = sorted((uuid.uuid4(), uuid.uuid4()), key=str)
    _set(monkeypatch, mode="strict", env_secret=None, default_org=None)
    _decrypt_map(monkeypatch, {"enc:a": "shared", "enc:b": "shared"})
    db = _FakeDB([_cfg(org_a, "enc:a"), _cfg(org_b, "enc:b")])
    with caplog.at_level(logging.ERROR, logger="app.api.dotmac_sub"):
        assert ds._resolve_webhook_org(db, BODY, _sig(BODY, "shared")) is None
    assert "ambiguous webhook binding" in caplog.text
    assert str(org_a) in caplog.text and str(org_b) in caplog.text


# ---------------------------------------------------------------------------
# shadow mode (default) — legacy decides, divergence is cutover evidence
# ---------------------------------------------------------------------------


def test_shadow_preserves_legacy_attribution_and_logs_divergence(monkeypatch, caplog):
    default_org, row_org = uuid.uuid4(), uuid.uuid4()
    _set(monkeypatch, mode="shadow", env_secret="env-s", default_org=str(default_org))
    # The same delivery also verifies against another org's binding — the
    # legacy default-org attribution wins, but the divergence must be logged.
    _decrypt_map(monkeypatch, {"enc:x": "env-s"})
    db = _FakeDB([_cfg(row_org)])
    with caplog.at_level(logging.WARNING, logger="app.api.dotmac_sub"):
        resolved = ds._resolve_webhook_org(
            db, BODY, _sig(BODY, "env-s"), delivery_id="dlv-42"
        )
    assert resolved == default_org
    assert "org-resolution divergence" in caplog.text
    assert str(default_org) in caplog.text
    assert str(row_org) in caplog.text
    assert "dlv-42" in caplog.text


def test_shadow_logs_when_only_legacy_resolves(monkeypatch, caplog):
    org = uuid.uuid4()
    _set(monkeypatch, mode="shadow", env_secret="env-s", default_org=str(org))
    db = _FakeDB([])
    with caplog.at_level(logging.WARNING, logger="app.api.dotmac_sub"):
        assert ds._resolve_webhook_org(db, BODY, _sig(BODY, "env-s")) == org
    assert "org-resolution divergence" in caplog.text
    assert "binding=None" in caplog.text


def test_shadow_silent_when_authorities_agree(monkeypatch, caplog):
    org = uuid.uuid4()
    _set(monkeypatch, mode="shadow", env_secret="env-s", default_org=str(org))
    # The default org's own binding carries the same secret (the seeded state).
    _decrypt_map(monkeypatch, {"enc:x": "env-s"})
    db = _FakeDB([_cfg(org)])
    with caplog.at_level(logging.WARNING, logger="app.api.dotmac_sub"):
        assert ds._resolve_webhook_org(db, BODY, _sig(BODY, "env-s")) == org
    assert "org-resolution divergence" not in caplog.text


# ---------------------------------------------------------------------------
# mode knob validation — unknown values fail loudly
# ---------------------------------------------------------------------------


def test_unknown_resolution_mode_fails_loudly(monkeypatch):
    _set(monkeypatch, mode="bogus", env_secret=None, default_org=None)
    with pytest.raises(ValueError, match="DOTMAC_SUB_WEBHOOK_ORG_RESOLUTION"):
        ds._resolve_webhook_org(_FakeDB([]), BODY, _sig(BODY, "x"))


def test_startup_validation_rejects_unknown_mode(monkeypatch):
    from app import startup

    monkeypatch.setattr(
        ds.settings, "dotmac_sub_webhook_org_resolution", "bogus", raising=False
    )
    errors = startup.validate_webhook_org_resolution_mode()
    assert errors and "DOTMAC_SUB_WEBHOOK_ORG_RESOLUTION" in errors[0]
    monkeypatch.setattr(
        ds.settings, "dotmac_sub_webhook_org_resolution", "strict", raising=False
    )
    assert startup.validate_webhook_org_resolution_mode() == []


# ---------------------------------------------------------------------------
# 503 gate — only when ZERO attribution authorities exist
# ---------------------------------------------------------------------------


class _FakeRequest:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def body(self) -> bytes:
        return self._payload

    async def json(self) -> dict:
        return json.loads(self._payload.decode("utf-8"))


def _call_handler(db):
    # No signature header: reaching the 400 "Missing signature" proves the
    # 503 configuration gate was passed.
    return asyncio.run(
        ds.dotmac_sub_webhook(
            request=_FakeRequest(BODY),
            x_webhook_signature_256=None,
            x_dotmacsub_signature=None,
            x_webhook_delivery_id=None,
            db=db,
        )
    )


def test_503_when_no_authority_exists(monkeypatch):
    _set(monkeypatch, mode="shadow", env_secret=None, default_org=None)
    with pytest.raises(HTTPException) as exc:
        _call_handler(_FakeDB([]))
    assert exc.value.status_code == 503


def test_env_secret_alone_is_an_authority_outside_strict(monkeypatch):
    _set(monkeypatch, mode="shadow", env_secret="env-s", default_org=None)
    with pytest.raises(HTTPException) as exc:
        _call_handler(_FakeDB([]))
    assert exc.value.status_code == 400  # gate passed; missing signature


def test_binding_rows_alone_are_an_authority(monkeypatch):
    """A config-row-only deployment can receive webhooks (no env secret)."""
    _set(monkeypatch, mode="shadow", env_secret=None, default_org=None)
    with pytest.raises(HTTPException) as exc:
        _call_handler(_FakeDB([_cfg(uuid.uuid4())]))
    assert exc.value.status_code == 400  # gate passed; missing signature


def test_strict_ignores_env_secret_for_the_503_gate(monkeypatch):
    _set(monkeypatch, mode="strict", env_secret="env-s", default_org=None)
    with pytest.raises(HTTPException) as exc:
        _call_handler(_FakeDB([]))
    assert exc.value.status_code == 503
