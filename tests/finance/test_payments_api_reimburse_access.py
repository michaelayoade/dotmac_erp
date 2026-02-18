from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.api.finance.payments import require_expense_reimburse_access
from app.services.finance.platform.authorization import AuthorizationService


def test_reimburse_access_allows_expense_reimburse_scope() -> None:
    auth = {
        "roles": [],
        "scopes": ["expense:claims:reimburse"],
        "person_id": None,
        "organization_id": None,
    }

    result = require_expense_reimburse_access(auth=auth, db=MagicMock())

    assert result is auth


def test_reimburse_access_db_fallback_checks_reimburse_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    person_id = str(uuid.uuid4())
    organization_id = str(uuid.uuid4())
    auth = {
        "roles": [],
        "scopes": [],
        "person_id": person_id,
        "organization_id": organization_id,
    }
    seen: dict[str, list[str]] = {}

    def _check_any_permission(db, pid, permissions, org_id):  # noqa: ANN001
        seen["permissions"] = permissions
        return True

    monkeypatch.setattr(
        AuthorizationService,
        "check_any_permission",
        _check_any_permission,
    )

    result = require_expense_reimburse_access(auth=auth, db=MagicMock())

    assert result is auth
    assert "expense:claims:reimburse" in seen["permissions"]


def test_reimburse_access_denies_without_any_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = {
        "roles": [],
        "scopes": [],
        "person_id": str(uuid.uuid4()),
        "organization_id": str(uuid.uuid4()),
    }

    monkeypatch.setattr(
        AuthorizationService,
        "check_any_permission",
        lambda *_args, **_kwargs: False,
    )

    with pytest.raises(HTTPException) as excinfo:
        require_expense_reimburse_access(auth=auth, db=MagicMock())

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "Forbidden"
