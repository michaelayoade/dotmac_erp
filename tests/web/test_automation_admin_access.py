"""Authorization and navigation canaries for the Automation Center."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, Request

from app.services.admin.settings_web import ADMIN_SETTINGS_SECTIONS
from app.web.deps import require_automation_access


def _request(method: str, path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [],
            "query_string": b"",
            "scheme": "https",
            "server": ("erp.example", 443),
            "client": ("127.0.0.1", 50000),
        }
    )


@pytest.mark.parametrize(
    ("method", "path", "permission"),
    [
        ("GET", "/automation/workflows", "automation:read"),
        ("POST", "/automation/workflows/new", "automation:create"),
        ("POST", "/automation/workflows/123/edit", "automation:update"),
        ("POST", "/automation/workflows/123/toggle", "automation:publish"),
        ("POST", "/automation/workflows/123/test", "automation:test"),
        ("POST", "/automation/executions/123/retry", "automation:retry"),
    ],
)
def test_automation_access_uses_action_specific_permission(
    method: str, path: str, permission: str
) -> None:
    auth = MagicMock()
    auth.has_permission.return_value = True

    assert require_automation_access(_request(method, path), auth) is auth
    auth.has_permission.assert_called_once_with(permission)


def test_automation_access_fails_closed() -> None:
    auth = MagicMock()
    auth.has_permission.return_value = False

    with pytest.raises(HTTPException) as raised:
        require_automation_access(_request("GET", "/automation"), auth)

    assert raised.value.status_code == 403


def test_admin_settings_owns_automation_navigation() -> None:
    automation = next(
        section
        for section in ADMIN_SETTINGS_SECTIONS
        if section["title"] == "Automation"
    )
    assert automation["url"] == "/automation"

    repo_root = Path(__file__).resolve().parents[2]
    admin_base = (repo_root / "templates/admin/base_admin.html").read_text()
    assert 'href="/automation"' in admin_base

    for template in (repo_root / "templates/finance/automation").glob("*.html"):
        assert '{% extends "admin/base_admin.html" %}' in template.read_text()
