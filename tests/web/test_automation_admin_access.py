"""Authorization and navigation canaries for the Automation Center."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, Request

from app.services.admin.settings_web import ADMIN_SETTINGS_SECTIONS
from app.web.automation import legacy_automation_path
from app.web.deps import require_automation_access
from app.web.finance.settings import legacy_automation_settings


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
        ("GET", "/automation/settings", "automation:read"),
        ("POST", "/automation/workflows/new", "automation:create"),
        ("POST", "/automation/settings", "automation:update"),
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

    automation_templates = repo_root / "templates/admin/automation"
    assert automation_templates.is_dir()
    assert not (repo_root / "templates/finance/automation").exists()

    for template in automation_templates.glob("*.html"):
        if template.name.startswith("_"):
            continue
        assert '{% extends "admin/base_admin.html" %}' in template.read_text()


def test_finance_router_does_not_mount_automation() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    finance_router_source = (repo_root / "app/web/finance/__init__.py").read_text(
        encoding="utf-8"
    )

    assert "automation_router" not in finance_router_source
    assert "app.web.finance.automation" not in finance_router_source


def test_finance_navigation_owns_recurring_transactions_not_automation() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    finance_base = (repo_root / "templates/finance/base_finance.html").read_text(
        encoding="utf-8"
    )

    assert 'href="/settings/recurring-transactions"' in finance_base
    assert 'href="/automation"' not in finance_base
    assert ">Recurring Transactions</span>" in finance_base


def test_settings_templates_are_separated_by_owner() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    recurring = (
        repo_root / "templates/finance/settings/recurring_transactions.html"
    ).read_text(encoding="utf-8")
    automation = (repo_root / "templates/admin/automation/settings.html").read_text(
        encoding="utf-8"
    )

    assert "recurring_default_frequency" in recurring
    assert "workflow_max_actions_per_event" not in recurring
    assert "workflow_max_actions_per_event" in automation
    assert "recurring_default_frequency" not in automation


def test_legacy_finance_automation_get_redirects_to_admin() -> None:
    response = legacy_automation_path(
        _request("GET", "/finance/automation/workflows"),
        "workflows",
    )

    assert response.status_code == 308
    assert response.headers["location"] == "/automation/workflows"


def test_legacy_finance_settings_redirect_to_recurring_transactions() -> None:
    response = legacy_automation_settings(MagicMock())

    assert response.status_code == 308
    assert response.headers["location"] == "/settings/recurring-transactions"


def test_automation_router_is_core_not_finance_gated() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    main_source = (repo_root / "app/main.py").read_text()
    core_mount = main_source.index("app.include_router(automation_web_router)")
    finance_gate = main_source.index('if is_module_enabled("finance"):')

    assert core_mount < finance_gate
