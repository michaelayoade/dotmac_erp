"""S1 foundation: least-privilege scopes on service ApiKeys.

An unscoped key (NULL/empty scopes) is grandfathered to full access so existing
keys keep working; a scoped key is restricted to exactly its scopes. The
require_service_scope dependency enforces this per endpoint.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.sync.dotmac_crm import require_crm_sync_enabled, require_service_scope
from app.models.auth import ApiKey
from app.models.sync.integration_config import IntegrationConfig, IntegrationType


def test_apikey_has_scope_grandfathers_unscoped_keys():
    key = ApiKey()
    key.scopes = None
    assert key.has_scope("crm:ncc:read") is True  # unscoped = full access
    key.scopes = []
    assert key.has_scope("anything") is True


def test_apikey_has_scope_restricts_scoped_keys():
    key = ApiKey()
    key.scopes = ["crm:ncc:read"]
    assert key.has_scope("crm:ncc:read") is True
    assert key.has_scope("crm:po:write") is False


def test_require_service_scope_allows_unscoped_key():
    dep = require_service_scope("crm:ncc:read")
    auth = {"scopes": []}
    assert dep(auth=auth) is auth


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


def test_require_crm_sync_enabled_rejects_inactive_config(db_session):
    import uuid

    org_id = uuid.uuid4()
    db_session.add(
        IntegrationConfig(
            organization_id=org_id,
            integration_type=IntegrationType.DOTMAC_CRM,
            base_url="https://crm.dotmac.io",
            is_active=False,
        )
    )
    db_session.flush()

    with pytest.raises(HTTPException) as exc:
        require_crm_sync_enabled(
            auth={"organization_id": org_id},
            db=db_session,
        )

    assert exc.value.status_code == 403
    assert "disabled" in exc.value.detail
