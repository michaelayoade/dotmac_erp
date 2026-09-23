from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.models.domain_settings import SettingDomain
from app.services.finance.settings_web import SettingsWebService


def test_update_email_settings_uses_existing_password_for_validation():
    service = SettingsWebService()
    db = MagicMock()

    data = {
        "smtp_host": "smtp.example.com",
        "smtp_password": "",
        "smtp_use_tls": "true",
        "smtp_use_ssl": "false",
    }

    with (
        patch.dict(
            "app.services.finance.settings_web.DOMAIN_SETTINGS_SERVICE",
            {SettingDomain.email: MagicMock()},
        ),
        patch("app.services.email._get_smtp_config") as get_config,
        patch("app.services.email.validate_smtp_config") as validate,
    ):
        get_config.return_value = {
            "host": "smtp.old.local",
            "port": 587,
            "username": "old-user",
            "password": "secret",
            "use_tls": True,
            "use_ssl": False,
            "from_email": "old@example.com",
            "from_name": "Old",
            "reply_to": None,
        }
        validate.return_value = (True, None)
        ok, error = service.update_email_settings(db, uuid.uuid4(), data)

    assert ok is True
    assert error is None
    validate.assert_called_once()
    config = validate.call_args[0][0]
    assert config["password"] == "secret"


def test_get_email_settings_context_reads_requested_organization_scope():
    service = SettingsWebService()
    db = MagicMock()
    db.scalar.return_value = None
    organization_id = uuid.uuid4()
    spec = SimpleNamespace(
        key="smtp_host",
        is_secret=False,
        default="",
        value_type=SimpleNamespace(value="string"),
    )

    with (
        patch(
            "app.services.finance.settings_web.list_specs",
            return_value=[spec],
        ),
        patch(
            "app.services.finance.settings_web.resolve_value",
            return_value="smtp.tenant.example",
        ) as resolve,
    ):
        context = service.get_email_settings_context(db, organization_id)

    resolve.assert_called_once_with(
        db,
        SettingDomain.email,
        "smtp_host",
        organization_id=organization_id,
    )
    assert context["settings"]["smtp_host"]["value"] == "smtp.tenant.example"
