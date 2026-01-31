from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from app.services.finance.settings_web import SettingsWebService
from app.models.domain_settings import SettingDomain


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
