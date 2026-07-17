"""Webhook org resolution (audit S4b): per-org secrets, default-org fallback.

The env secret authenticates the env-configured default org (single-org
behaviour unchanged); otherwise each active per-org IntegrationConfig
(DOTMAC_SUB) api_secret is tried — a match both authenticates and resolves the
org, so a second org is a config row, not a code change.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from types import SimpleNamespace

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


def test_env_secret_resolves_default_org(monkeypatch):
    org = uuid.uuid4()
    monkeypatch.setattr(
        ds.settings, "dotmac_sub_webhook_secret", "env-s", raising=False
    )
    monkeypatch.setattr(ds.settings, "default_organization_id", str(org), raising=False)
    body = b'{"event_type":"x"}'
    assert ds._resolve_webhook_org(_FakeDB([]), body, _sig(body, "env-s")) == org


def test_per_org_secret_resolves_that_org(monkeypatch):
    org = uuid.uuid4()
    monkeypatch.setattr(
        ds.settings, "dotmac_sub_webhook_secret", "env-s", raising=False
    )
    monkeypatch.setattr(ds.settings, "default_organization_id", None, raising=False)
    cfg = SimpleNamespace(organization_id=org, api_secret="enc:whatever")
    monkeypatch.setattr(
        "app.services.integration_config.decrypt_credential",
        lambda value, db=None: "org-secret",
    )
    body = b'{"event_type":"x"}'
    assert (
        ds._resolve_webhook_org(_FakeDB([cfg]), body, _sig(body, "org-secret")) == org
    )


def test_nothing_verifies_returns_none(monkeypatch):
    monkeypatch.setattr(
        ds.settings, "dotmac_sub_webhook_secret", "env-s", raising=False
    )
    monkeypatch.setattr(
        ds.settings, "default_organization_id", str(uuid.uuid4()), raising=False
    )
    monkeypatch.setattr(
        "app.services.integration_config.decrypt_credential",
        lambda value, db=None: "org-secret",
    )
    cfg = SimpleNamespace(organization_id=uuid.uuid4(), api_secret="enc:x")
    body = b'{"event_type":"x"}'
    assert ds._resolve_webhook_org(_FakeDB([cfg]), body, _sig(body, "wrong")) is None
