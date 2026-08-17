"""Tests for startup validation helpers.

`warn_unconfigured_webhook_allowlist` used to ask both of its questions inside
one `allow_cross_org` block, and the two tests below could only assert the
answers. The split gives them two seams to assert the SCOPE of each question
against, which is what the last two tests here do and what the old shape could
not state at all.
"""

import logging
import re
from unittest.mock import MagicMock

import pytest

import app.services.finance.automation.webhook_policy as webhook_policy
from app import startup
from app.services.finance.automation.webhook_policy import WebhookCeiling

CONFIGURED_CEILING = WebhookCeiling(allowed_hosts=frozenset({"hooks.example.com"}))
UNCONFIGURED_CEILING = WebhookCeiling()


def test_warn_unconfigured_webhook_allowlist_logs_warning(monkeypatch, caplog):
    mock_db = MagicMock()
    monkeypatch.setattr(
        startup, "read_platform_webhook_ceiling", lambda db: UNCONFIGURED_CEILING
    )
    monkeypatch.setattr(startup, "any_tenant_has_an_active_webhook_rule", lambda: True)

    with caplog.at_level(logging.WARNING):
        startup.warn_unconfigured_webhook_allowlist(mock_db)

    assert "Active webhook automation rules exist" in caplog.text


def test_warn_unconfigured_webhook_allowlist_no_warning_when_configured(
    monkeypatch, caplog
):
    mock_db = MagicMock()
    monkeypatch.setattr(
        startup, "read_platform_webhook_ceiling", lambda db: CONFIGURED_CEILING
    )
    monkeypatch.setattr(startup, "any_tenant_has_an_active_webhook_rule", lambda: True)

    with caplog.at_level(logging.WARNING):
        startup.warn_unconfigured_webhook_allowlist(mock_db)

    assert "Active webhook automation rules exist" not in caplog.text


@pytest.mark.timeout(300)
def test_the_outage_warning_names_a_route_that_exists(monkeypatch):
    """The 2am instruction is executed as written, so it is checked as written.

    An earlier draft told the operator to `PUT /settings/automation/...` — the
    unprefixed namespace, which belongs to the HTML router and answers 405. It
    also offered the process environment as an alternative runtime recovery,
    but that input belongs solely to create-if-missing startup seeding. Both
    mistakes are asserted against here: the route is
    matched against the app's REAL route table rather than a literal, so a
    remount that changes the prefix fails this test instead of the operator.
    """
    from app.main import app as fastapi_app

    monkeypatch.setattr(
        startup, "read_platform_webhook_ceiling", lambda db: UNCONFIGURED_CEILING
    )
    monkeypatch.setattr(startup, "any_tenant_has_an_active_webhook_rule", lambda: True)
    warnings: list[str] = []
    monkeypatch.setattr(startup.logger, "warning", warnings.append)

    startup.warn_unconfigured_webhook_allowlist(MagicMock())

    warning = "\n".join(warnings)
    quoted = [t.rstrip(".,;:") for t in re.findall(r"PUT (\S+)", warning)]
    assert quoted, "the warning offers no recovery route at all"

    for path in quoted:
        served = [
            route
            for route in fastapi_app.router.routes
            if getattr(route, "path_regex", None) is not None
            and route.path_regex.match(path)
        ]
        assert served, (
            f"the warning tells an operator to PUT {path}, which this app does "
            "not route at all — following it verbatim 404s and stays down"
        )
        writable = [
            route
            for route in served
            if "PUT" in (getattr(route, "methods", None) or set())
        ]
        assert writable, (
            f"{path} is routed, but by no PUT handler (it matches "
            f"{[r.path for r in served]}) — verbatim, that is a 405"
        )

    # The dead environment fallback must not be re-offered as a recovery: the
    # message may name the variables ONLY to say they do not work.
    assert "Set WEBHOOK_ALLOWED_HOSTS and/or" not in warning
    assert "does NOT end this" in warning
    assert '"is_active": true' in warning


def test_the_ceiling_read_never_consults_an_organization_row(monkeypatch):
    """The platform scope is STATED, not inherited from an empty context.

    The retired code read the platform row only because process startup happens
    to carry no ambient organization — swap in a session that does carry one and
    it would have read that organization's row instead. This drives the real
    `read_platform_webhook_ceiling` and records the scope of every settings read
    it makes.
    """
    scopes: list[object] = []

    def _record_scope(db, domain, key, *, organization_id=None):
        scopes.append(organization_id)
        return None

    monkeypatch.setattr(webhook_policy, "resolve_value", _record_scope)
    monkeypatch.setattr(startup, "any_tenant_has_an_active_webhook_rule", lambda: False)

    startup.warn_unconfigured_webhook_allowlist(MagicMock())

    assert scopes, "the ceiling read never reached the settings resolver"
    assert set(scopes) == {None}


def test_the_discovery_runs_only_when_the_ceiling_is_unconfigured(monkeypatch):
    """A correctly configured deployment opens no organization session here.

    The discovery enumerates every organization and opens a session per
    organization. That is acceptable once per process only because it is gated:
    a configured ceiling returns before the enumeration starts.
    """
    calls = {"count": 0}

    def _discover():
        calls["count"] += 1
        return True

    monkeypatch.setattr(startup, "any_tenant_has_an_active_webhook_rule", _discover)

    monkeypatch.setattr(
        startup, "read_platform_webhook_ceiling", lambda db: CONFIGURED_CEILING
    )
    startup.warn_unconfigured_webhook_allowlist(MagicMock())
    assert calls["count"] == 0

    monkeypatch.setattr(
        startup, "read_platform_webhook_ceiling", lambda db: UNCONFIGURED_CEILING
    )
    startup.warn_unconfigured_webhook_allowlist(MagicMock())
    assert calls["count"] == 1


def test_validate_startup_does_not_warn_before_settings_are_seeded(monkeypatch):
    mock_db = MagicMock()
    called = {"value": False}

    monkeypatch.setattr(startup, "validate_required_config", lambda: [])
    monkeypatch.setattr(startup, "validate_openbao_connectivity", lambda: [])
    monkeypatch.setattr(startup, "validate_required_secrets", lambda db=None: [])

    def _mark_called(db=None):
        called["value"] = True

    monkeypatch.setattr(startup, "warn_unconfigured_webhook_allowlist", _mark_called)

    assert startup.validate_startup(mock_db, exit_on_failure=False) is True
    assert called["value"] is False


def test_application_startup_warns_only_after_settings_seed():
    """The first-boot environment seed must precede the outage decision."""
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).parents[1] / "app/main.py").read_text())
    lifespan = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "lifespan"
    )
    calls = {
        node.func.id: node.lineno
        for node in ast.walk(lifespan)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert calls["seed_all_settings"] < calls["warn_unconfigured_webhook_allowlist"]
