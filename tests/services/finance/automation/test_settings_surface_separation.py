"""Settings forms only read and write the fields owned by their UI surface."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.models.domain_settings import SettingDomain
from app.services.finance.settings_web import (
    ADMIN_AUTOMATION_SETTING_KEYS,
    RECURRING_TRANSACTION_SETTING_KEYS,
    settings_web_service,
)
from app.services.settings_spec import DOMAIN_SETTINGS_SERVICE


class _RecordingService:
    def __init__(self) -> None:
        self.written_keys: list[str] = []

    def upsert_by_key(self, db, key, payload, *args, **kwargs):
        self.written_keys.append(key)
        return MagicMock()


@pytest.fixture()
def recording_service():
    original = DOMAIN_SETTINGS_SERVICE[SettingDomain.automation]
    service = _RecordingService()
    DOMAIN_SETTINGS_SERVICE[SettingDomain.automation] = service
    try:
        yield service
    finally:
        DOMAIN_SETTINGS_SERVICE[SettingDomain.automation] = original


def test_recurring_context_resolves_only_finance_defaults() -> None:
    with patch(
        "app.services.finance.settings_web.resolve_value", return_value=None
    ) as resolve:
        context = settings_web_service.get_recurring_transaction_settings_context(
            MagicMock(), uuid4()
        )

    assert set(context["settings"]) == RECURRING_TRANSACTION_SETTING_KEYS
    assert {call.args[2] for call in resolve.call_args_list} == (
        RECURRING_TRANSACTION_SETTING_KEYS
    )


def test_admin_context_excludes_finance_recurring_defaults() -> None:
    with patch("app.services.finance.settings_web.resolve_value", return_value=None):
        context = settings_web_service.get_admin_automation_settings_context(
            MagicMock(), uuid4()
        )

    assert set(context["settings"]) == ADMIN_AUTOMATION_SETTING_KEYS
    assert not set(context["settings"]) & RECURRING_TRANSACTION_SETTING_KEYS


def test_recurring_update_ignores_admin_fields(recording_service) -> None:
    db = MagicMock()
    settings_web_service.update_recurring_transaction_settings(
        db,
        uuid4(),
        {
            "recurring_lookback_days": "14",
            "workflow_max_actions_per_event": "99",
            "webhook_timeout_seconds": "60",
        },
    )

    assert recording_service.written_keys == ["recurring_lookback_days"]


def test_admin_update_ignores_finance_and_unrendered_fields(recording_service) -> None:
    db = MagicMock()
    settings_web_service.update_admin_automation_settings(
        db,
        uuid4(),
        {
            "workflow_max_actions_per_event": "12",
            "recurring_lookback_days": "14",
            "fa_depreciation_auto_run_enabled": "true",
        },
    )

    assert recording_service.written_keys == ["workflow_max_actions_per_event"]
