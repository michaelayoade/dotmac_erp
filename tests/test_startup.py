"""Tests for startup validation helpers."""

import logging
from unittest.mock import MagicMock

from app import startup


def test_warn_unconfigured_webhook_allowlist_logs_warning(monkeypatch, caplog):
    mock_db = MagicMock()
    monkeypatch.setattr(
        startup, "webhook_allowlist_configured", lambda db: False
    )
    monkeypatch.setattr(startup, "has_active_webhook_actions", lambda db: True)

    with caplog.at_level(logging.WARNING):
        startup.warn_unconfigured_webhook_allowlist(mock_db)

    assert "Active webhook automation rules exist" in caplog.text


def test_warn_unconfigured_webhook_allowlist_no_warning_when_configured(
    monkeypatch, caplog
):
    mock_db = MagicMock()
    monkeypatch.setattr(startup, "webhook_allowlist_configured", lambda db: True)
    monkeypatch.setattr(startup, "has_active_webhook_actions", lambda db: True)

    with caplog.at_level(logging.WARNING):
        startup.warn_unconfigured_webhook_allowlist(mock_db)

    assert "Active webhook automation rules exist" not in caplog.text


def test_validate_startup_invokes_webhook_allowlist_warning(monkeypatch):
    mock_db = MagicMock()
    called = {"value": False}

    monkeypatch.setattr(startup, "validate_required_config", lambda: [])
    monkeypatch.setattr(startup, "validate_openbao_connectivity", lambda: [])
    monkeypatch.setattr(startup, "validate_required_secrets", lambda db=None: [])

    def _mark_called(db=None):
        called["value"] = True

    monkeypatch.setattr(startup, "warn_unconfigured_webhook_allowlist", _mark_called)

    assert startup.validate_startup(mock_db, exit_on_failure=False) is True
    assert called["value"] is True
