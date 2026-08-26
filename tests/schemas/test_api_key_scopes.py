"""Service credentials require visible, explicit leaf scopes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.auth import (
    ApiKeyCreate,
    ApiKeyGenerateRequest,
    ApiKeyRead,
    ApiKeyUpdate,
)


@pytest.mark.parametrize(
    "factory",
    (
        lambda: ApiKeyCreate(key_hash="secret", scopes=[]),
        lambda: ApiKeyGenerateRequest(scopes=[]),
    ),
)
def test_new_service_credentials_require_at_least_one_scope(factory):
    with pytest.raises(ValidationError):
        factory()


@pytest.mark.parametrize(
    "scope",
    ("", "*", "network:*", "missing-colon", "UPPER:read"),
)
def test_new_service_credentials_require_leaf_scope_names(scope):
    with pytest.raises(ValidationError):
        ApiKeyGenerateRequest(scopes=[scope])


def test_new_and_updated_service_credentials_accept_explicit_leaf_scopes():
    scopes = ["sub:domain:write", "sub:inventory:read"]
    assert ApiKeyGenerateRequest(scopes=scopes).scopes == scopes
    assert ApiKeyCreate(key_hash="secret", scopes=scopes).scopes == scopes
    assert ApiKeyUpdate(scopes=scopes).scopes == scopes


def test_api_key_read_exposes_scopes_for_the_legacy_key_audit():
    response = ApiKeyRead(
        id=uuid.uuid4(),
        key_hash="hashed-secret",
        scopes=None,
        created_at=datetime.now(UTC),
    )

    assert response.model_dump()["scopes"] is None
