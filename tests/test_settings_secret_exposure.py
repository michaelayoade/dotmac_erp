"""Settings secret-exposure guards.

The settings substrate stores secret values (payment-provider keys,
``jwt_secret``, …) as plaintext in ``domain_settings.value_text``, and copies
them verbatim into the history table on every change. ``is_secret`` is a
display hint, not a mask.

These tests pin the three properties that keep those values from leaving the
server: history responses mask secrets, history entries are tenant-scoped, and
the admin web surface requires an administrator.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.settings import _can_read_history_entry, _can_restore_history_entry
from app.schemas.settings import MASKED_VALUE, SettingHistoryRead
from app.web.deps import require_admin_access


def _history_entry(**overrides):
    values = {
        "id": uuid4(),
        "setting_id": uuid4(),
        "domain": "banking",
        "key": "mono_secret_key",
        "action": "UPDATE",
        "old_value_text": "live_sk_OLD",
        "old_is_secret": True,
        "new_value_text": "live_sk_NEW",
        "new_is_secret": True,
        "changed_at": datetime(2026, 7, 12, 9, 0, 0),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TestHistoryMasksSecrets:
    def test_secret_values_are_masked_in_both_directions(self):
        """A secret's value must never be serialized — old or new.

        Without this, ``GET /api/v1/settings/history?domain=banking&
        key=mono_secret_key`` hands back the live Mono secret key in plaintext,
        defeating the write-only field in the admin UI. The same route reaches
        ``jwt_secret`` and the Paystack keys.
        """
        read = SettingHistoryRead.model_validate(_history_entry())

        assert read.old_value_text == MASKED_VALUE
        assert read.new_value_text == MASKED_VALUE
        # The audit trail still records THAT it changed, and who changed it.
        assert read.key == "mono_secret_key"
        assert read.action == "UPDATE"

    def test_non_secret_values_are_untouched(self):
        """Masking must not blind the audit trail for ordinary settings."""
        read = SettingHistoryRead.model_validate(
            _history_entry(
                key="mono_enabled",
                old_value_text="false",
                old_is_secret=False,
                new_value_text="true",
                new_is_secret=False,
            )
        )

        assert read.old_value_text == "false"
        assert read.new_value_text == "true"

    def test_a_value_that_became_secret_masks_its_old_plaintext(self):
        """Flags are read per-side, so a key flipped to secret still hides the
        plaintext it used to hold."""
        read = SettingHistoryRead.model_validate(
            _history_entry(
                old_value_text="was_plaintext",
                old_is_secret=True,
                new_value_text="now_secret",
                new_is_secret=True,
            )
        )

        assert read.old_value_text == MASKED_VALUE
        assert read.new_value_text == MASKED_VALUE


class TestHistoryIsTenantScoped:
    """Settings routes use the ORM cross-org marker, not an RLS bypass.

    The current settings-history table has no PostgreSQL RLS, so this ownership
    check is the application boundary between tenants — including for a
    cross-tenant write through ``POST /history/restore``. Future database RLS
    remains independently effective.
    """

    def test_entry_from_another_organization_is_not_owned(self):
        auth = {"organization_id": str(uuid4())}
        entry = _history_entry(organization_id=uuid4())

        assert _can_read_history_entry(entry, auth) is False
        assert _can_restore_history_entry(entry, auth, MagicMock()) is False

    def test_entry_from_the_callers_organization_is_owned(self):
        org = uuid4()
        auth = {"organization_id": str(org)}
        entry = _history_entry(organization_id=org)

        assert _can_read_history_entry(entry, auth) is True
        assert _can_restore_history_entry(entry, auth, MagicMock()) is True

    def test_global_setting_is_readable(self):
        """Global settings carry a NULL organization_id and stay visible."""
        auth = {"organization_id": str(uuid4())}
        entry = _history_entry(organization_id=None)

        assert _can_read_history_entry(entry, auth) is True

    def test_global_setting_is_not_restorable_by_a_tenant_administrator(self):
        """Reading a platform row's history is not permission to rewrite it.

        `restore_from_history` resolves its target scope from the entry, so a
        NULL-org entry rewrites the platform row. `settings:manage` is a TENANT
        permission; without this split a tenant admin could roll a
        platform-owned SSRF control back to any value it ever held.
        """
        person_id = uuid4()
        auth = {"organization_id": str(uuid4()), "person_id": str(person_id)}
        entry = _history_entry(organization_id=None)
        db = MagicMock()

        assert _can_read_history_entry(entry, auth) is True
        with patch(
            "app.api.settings.has_live_admin_grant", return_value=False
        ) as grant:
            assert _can_restore_history_entry(entry, auth, db) is False
        grant.assert_called_once_with(db, str(person_id))

    def test_global_setting_is_restorable_by_a_platform_administrator(self):
        person_id = uuid4()
        auth = {"organization_id": str(uuid4()), "person_id": str(person_id)}
        entry = _history_entry(organization_id=None)
        db = MagicMock()

        with patch("app.api.settings.has_live_admin_grant", return_value=True) as grant:
            assert _can_restore_history_entry(entry, auth, db) is True
        grant.assert_called_once_with(db, str(person_id))

    def test_missing_person_id_denies_rather_than_raises(self):
        """An unresolved actor cannot hold the live platform grant."""
        entry = _history_entry(organization_id=None)
        db = MagicMock()

        assert _can_restore_history_entry(entry, {}, db) is False
        assert _can_restore_history_entry(entry, {"person_id": None}, db) is False


class TestAdminSurfaceRequiresAdmin:
    """Every route under /admin depended only on ``optional_web_auth``, which —
    as the name says — requires nothing. The sole check was
    ``if auth and auth.organization_id``, so any authenticated user of any role
    could open the admin settings pages and POST to them, credential forms
    included.
    """

    def test_non_admin_is_refused(self):
        auth = SimpleNamespace(roles=["warehouse_operator"], is_admin=False)

        with pytest.raises(HTTPException) as exc:
            require_admin_access(auth)

        assert exc.value.status_code == 403

    def test_admin_is_allowed(self):
        auth = SimpleNamespace(roles=["admin"], is_admin=True)

        assert require_admin_access(auth) is auth
