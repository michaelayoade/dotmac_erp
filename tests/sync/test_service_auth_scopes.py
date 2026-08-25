"""Least-privilege scopes on service API keys."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.service_principal import require_any_service_scope, require_service_scope
from app.models.auth import ApiKey


def test_apikey_has_scope_refuses_unscoped_keys() -> None:
    key = ApiKey()
    key.scopes = None
    assert key.has_scope("sub:inventory:read") is False
    key.scopes = []
    assert key.has_scope("anything") is False


def test_apikey_has_scope_restricts_scoped_keys() -> None:
    key = ApiKey()
    key.scopes = ["sub:inventory:read"]
    assert key.has_scope("sub:inventory:read") is True
    assert key.has_scope("sub:po:write") is False


def test_require_service_scope_refuses_unscoped_key() -> None:
    dep = require_service_scope("sub:inventory:read")
    with pytest.raises(HTTPException) as exc:
        dep(auth={"scopes": []})
    assert exc.value.status_code == 403
    assert "sub:inventory:read" in exc.value.detail


def test_require_any_service_scope_refuses_unscoped_key() -> None:
    dep = require_any_service_scope("sub:domain:write", "sub:inventory:read")
    with pytest.raises(HTTPException) as exc:
        dep(auth={"scopes": None})
    assert exc.value.status_code == 403
    assert "sub:domain:write" in exc.value.detail


def test_require_service_scope_allows_key_with_scope() -> None:
    dep = require_service_scope("sub:po:write")
    auth = {"scopes": ["sub:po:write", "sub:inventory:read"]}
    assert dep(auth=auth) is auth


def test_require_service_scope_rejects_key_missing_scope() -> None:
    dep = require_service_scope("sub:inventory:read")
    auth = {"scopes": ["sub:material:read"]}
    with pytest.raises(HTTPException) as exc:
        dep(auth=auth)
    assert exc.value.status_code == 403
    assert "sub:inventory:read" in exc.value.detail
