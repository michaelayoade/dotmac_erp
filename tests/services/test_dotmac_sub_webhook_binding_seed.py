"""Startup seed: migrate the env webhook secret into the default org's binding.

Audit D2: per-org IntegrationConfig(DOTMAC_SUB) rows are the webhook
org-attribution authority. When the resolution mode is not ``legacy`` and the
env secret + DEFAULT_ORGANIZATION_ID are set but the default org has no active
binding, the startup seed creates one carrying the env secret (encrypted
exactly as the admin UI stores it). Idempotent: a second boot no-ops.

Hermetic: throwaway in-memory SQLite engine translating the ``sync`` /
``core_org`` schemas to the default one, creating only ``integration_config``
(SQLite does not validate the org FK target at DDL time).
"""

from __future__ import annotations

import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.models.sync.integration_config import IntegrationConfig, IntegrationType
from app.services.integration_config import (
    IntegrationConfigService,
    decrypt_credential,
    seed_dotmac_sub_webhook_binding,
)

_ORG = uuid.UUID("00000000-0000-0000-0000-0000000000d2")
_SECRET = "env-webhook-secret"


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        execution_options={"schema_translate_map": {"sync": None, "core_org": None}},
    )
    # SQLite can't parse Postgres server-defaults like gen_random_uuid(); drop
    # them (Python-side defaults still supply the PK), mirroring the shared harness.
    for col in IntegrationConfig.__table__.columns:
        default = col.server_default
        if default is not None and "gen_random_uuid" in str(
            getattr(default, "arg", default)
        ):
            col.server_default = None
    IntegrationConfig.__table__.create(engine)
    maker = sessionmaker(bind=engine)
    db = maker()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def seed_env(monkeypatch):
    monkeypatch.setenv("INTEGRATION_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(
        settings, "dotmac_sub_webhook_org_resolution", "shadow", raising=False
    )
    monkeypatch.setattr(settings, "dotmac_sub_webhook_secret", _SECRET, raising=False)
    monkeypatch.setattr(settings, "default_organization_id", str(_ORG), raising=False)
    monkeypatch.setattr(
        settings, "dotmac_sub_api_url", "https://sub.example.com", raising=False
    )
    return monkeypatch


def _rows(db):
    return list(db.execute(select(IntegrationConfig)).scalars().all())


def test_seed_creates_encrypted_binding_once(session, seed_env):
    created = seed_dotmac_sub_webhook_binding(session)
    assert created is not None

    rows = _rows(session)
    assert len(rows) == 1
    row = rows[0]
    assert row.organization_id == _ORG
    assert row.integration_type == IntegrationType.DOTMAC_SUB
    assert row.is_active is True
    assert row.base_url == "https://sub.example.com"
    # Stored encrypted (exactly as the admin UI would), and round-trips.
    assert row.api_secret.startswith("enc:")
    assert row.api_secret != _SECRET
    assert decrypt_credential(row.api_secret, session) == _SECRET


def test_seed_is_idempotent_on_second_boot(session, seed_env):
    assert seed_dotmac_sub_webhook_binding(session) is not None
    assert seed_dotmac_sub_webhook_binding(session) is None
    assert len(_rows(session)) == 1


def test_seed_skips_when_an_active_binding_already_exists(session, seed_env):
    IntegrationConfigService(session).create_config(
        organization_id=_ORG,
        integration_type=IntegrationType.DOTMAC_SUB,
        base_url="https://sub.example.com",
        api_key="",
        api_secret="admin-managed-secret",
    )
    session.commit()
    assert seed_dotmac_sub_webhook_binding(session) is None
    rows = _rows(session)
    assert len(rows) == 1
    assert decrypt_credential(rows[0].api_secret, session) == "admin-managed-secret"


def test_seed_noop_in_legacy_mode(session, seed_env):
    seed_env.setattr(
        settings, "dotmac_sub_webhook_org_resolution", "legacy", raising=False
    )
    assert seed_dotmac_sub_webhook_binding(session) is None
    assert _rows(session) == []


def test_seed_noop_without_env_secret_or_default_org(session, seed_env):
    seed_env.setattr(settings, "dotmac_sub_webhook_secret", None, raising=False)
    assert seed_dotmac_sub_webhook_binding(session) is None

    seed_env.setattr(settings, "dotmac_sub_webhook_secret", _SECRET, raising=False)
    seed_env.setattr(settings, "default_organization_id", None, raising=False)
    assert seed_dotmac_sub_webhook_binding(session) is None
    assert _rows(session) == []
