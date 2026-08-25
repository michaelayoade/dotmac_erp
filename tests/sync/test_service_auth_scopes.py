"""Least-privilege scopes on service ApiKeys.

Authentication does not imply authority: a key with NULL/empty scopes may be
identified for an operator audit, but every service operation refuses it.
"""

from __future__ import annotations

import types
import uuid

import pytest
from fastapi import HTTPException

from app.api.sync.dotmac_crm import (
    require_any_service_scope,
    require_crm_sync_enabled,
    require_service_scope,
)
from app.models.auth import ApiKey


def test_apikey_has_scope_refuses_unscoped_keys():
    key = ApiKey()
    key.scopes = None
    assert key.has_scope("crm:ncc:read") is False
    key.scopes = []
    assert key.has_scope("anything") is False


def test_apikey_has_scope_restricts_scoped_keys():
    key = ApiKey()
    key.scopes = ["crm:ncc:read"]
    assert key.has_scope("crm:ncc:read") is True
    assert key.has_scope("crm:po:write") is False


def test_require_service_scope_refuses_unscoped_key():
    dep = require_service_scope("crm:ncc:read")
    with pytest.raises(HTTPException) as exc:
        dep(auth={"scopes": []})
    assert exc.value.status_code == 403
    assert "crm:ncc:read" in exc.value.detail


def test_require_any_service_scope_refuses_unscoped_key():
    dep = require_any_service_scope("crm:sync:write", "crm:write")
    with pytest.raises(HTTPException) as exc:
        dep(auth={"scopes": None})
    assert exc.value.status_code == 403
    assert "crm:sync:write" in exc.value.detail


def test_require_service_scope_allows_key_with_scope():
    dep = require_service_scope("crm:po:write")
    auth = {"scopes": ["crm:po:write", "crm:ncc:read"]}
    assert dep(auth=auth) is auth


def test_require_service_scope_rejects_key_missing_scope():
    dep = require_service_scope("crm:ncc:read")
    auth = {"scopes": ["crm:inventory:read"]}
    with pytest.raises(HTTPException) as exc:
        dep(auth=auth)
    assert exc.value.status_code == 403
    assert "crm:ncc:read" in exc.value.detail


def test_require_crm_sync_enabled_rejects_inactive_config():
    org_id = uuid.uuid4()
    db = types.SimpleNamespace(
        scalar=lambda _stmt: types.SimpleNamespace(is_active=False)
    )

    with pytest.raises(HTTPException) as exc:
        require_crm_sync_enabled(
            auth={"organization_id": org_id},
            db=db,
        )

    assert exc.value.status_code == 403
    assert "disabled" in exc.value.detail
