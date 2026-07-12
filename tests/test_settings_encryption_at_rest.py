"""Secret domain settings are encrypted at rest.

``DomainSetting.value_text`` is a plain ``Text`` column, so every secret the
settings table holds — ``mono_secret_key``, ``paystack_secret_key``,
``jwt_secret``, SMTP passwords — was stored in plaintext, and the history table
kept a second plaintext copy per change.

Encryption is applied at the ORM boundary, so what matters is the round trip:
ciphertext in the database, plaintext in memory, and no call site aware of the
difference. These tests assert against the *raw column* (via plain SQL, bypassing
the ORM) — asserting through the ORM would only prove the ORM agrees with itself.
"""

import os
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from app.models.domain_settings import DomainSetting, SettingDomain, SettingValueType
from app.services.settings_crypto import (
    decrypt_value,
    encrypt_value,
    is_bootstrap_key,
    should_encrypt,
)

TEST_KEY = Fernet.generate_key().decode()


@pytest.fixture
def key_env():
    with patch.dict(os.environ, {"INTEGRATION_ENCRYPTION_KEY": TEST_KEY}):
        yield


def _raw_value(db, setting_id) -> str | None:
    """Read the column straight from the DB, bypassing the ORM listeners."""
    return db.execute(
        text("SELECT value_text FROM domain_settings WHERE id = :id"),
        {"id": str(setting_id)},
    ).scalar()


class TestCryptoPrimitives:
    def test_round_trip(self, key_env):
        token = encrypt_value("live_sk_supersecret")

        assert token != "live_sk_supersecret"
        assert token.startswith("enc:")
        assert decrypt_value(token) == "live_sk_supersecret"

    def test_encryption_is_idempotent(self, key_env):
        """A re-flush must not double-encrypt — the second pass would produce a
        value that decrypts to ciphertext rather than to the secret."""
        once = encrypt_value("live_sk_x")
        twice = encrypt_value(once)

        assert twice == once
        assert decrypt_value(twice) == "live_sk_x"

    def test_decrypt_tolerates_legacy_plaintext(self, key_env):
        """Rows written before this existed must stay readable until the
        backfill runs."""
        assert decrypt_value("live_sk_plaintext") == "live_sk_plaintext"

    def test_openbao_references_are_not_encrypted(self, key_env):
        """A bao:// ref is a pointer, not a secret — encrypting it would break
        resolution at runtime."""
        ref = "bao://secret/dotmac#mono_key"

        assert encrypt_value(ref) == ref

    def test_missing_key_stores_plaintext_rather_than_breaking(self):
        """No key configured must not make settings unsavable. It degrades to
        the status quo, loudly."""
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("app.services.settings_crypto._fernet", return_value=None),
        ):
            assert encrypt_value("live_sk_x") == "live_sk_x"

    def test_wrong_key_returns_none_rather_than_garbage(self, key_env):
        token = encrypt_value("live_sk_x")

        with patch.dict(
            os.environ, {"INTEGRATION_ENCRYPTION_KEY": Fernet.generate_key().decode()}
        ):
            assert decrypt_value(token) is None


class TestBootstrapKeyIsNeverEncrypted:
    """``totp_encryption_key`` IS the encryption key on deployments that do not
    set it in the environment — ``auth_flow._mfa_key`` falls back to reading it
    out of this very table. Encrypting it with itself is circular: you would
    need the value in order to recover the value.
    """

    def test_bootstrap_key_is_recognised(self):
        assert is_bootstrap_key("totp_encryption_key") is True
        assert is_bootstrap_key("mono_secret_key") is False

    def test_bootstrap_key_is_not_encrypted_even_though_it_is_secret(self):
        setting = DomainSetting(
            domain=SettingDomain.auth,
            key="totp_encryption_key",
            is_secret=True,
        )

        assert should_encrypt(setting) is False

    def test_ordinary_secret_is_encrypted(self):
        setting = DomainSetting(
            domain=SettingDomain.banking,
            key="mono_secret_key",
            is_secret=True,
        )

        assert should_encrypt(setting) is True

    def test_non_secret_is_not_encrypted(self):
        setting = DomainSetting(
            domain=SettingDomain.banking,
            key="mono_enabled",
            is_secret=False,
        )

        assert should_encrypt(setting) is False


class TestOrmRoundTrip:
    """The property that actually matters: ciphertext on disk, plaintext in
    memory, callers unchanged."""

    def test_secret_is_ciphertext_in_the_database(self, db_session, key_env):
        setting = DomainSetting(
            domain=SettingDomain.banking,
            key="mono_secret_key",
            value_type=SettingValueType.string,
            value_text="live_sk_supersecret",
            is_secret=True,
        )
        db_session.add(setting)
        db_session.commit()

        stored = _raw_value(db_session, setting.id)

        assert stored.startswith("enc:")
        assert "live_sk_supersecret" not in stored

    def test_secret_reads_back_as_plaintext(self, db_session, key_env):
        setting = DomainSetting(
            domain=SettingDomain.banking,
            key="mono_secret_key",
            value_type=SettingValueType.string,
            value_text="live_sk_supersecret",
            is_secret=True,
        )
        db_session.add(setting)
        db_session.commit()
        setting_id = setting.id
        db_session.expunge_all()

        loaded = db_session.get(DomainSetting, setting_id)

        # Every one of the ~170 call sites that read .value_text gets this.
        assert loaded.value_text == "live_sk_supersecret"

    def test_non_secret_is_left_readable_in_the_database(self, db_session, key_env):
        """Encrypting ordinary settings would make the DB opaque for no gain."""
        setting = DomainSetting(
            domain=SettingDomain.banking,
            key="mono_enabled",
            value_type=SettingValueType.string,
            value_text="true",
            is_secret=False,
        )
        db_session.add(setting)
        db_session.commit()

        assert _raw_value(db_session, setting.id) == "true"

    def test_load_does_not_rewrite_plaintext_over_the_ciphertext(
        self, db_session, key_env
    ):
        """Decrypting on load must not mark the row dirty.

        If it did, any later unrelated flush would write the decrypted plaintext
        straight back over the ciphertext — silently undoing the encryption for
        every secret that had merely been *read*. Hence set_committed_value.
        """
        setting = DomainSetting(
            domain=SettingDomain.banking,
            key="mono_secret_key",
            value_type=SettingValueType.string,
            value_text="live_sk_supersecret",
            is_secret=True,
        )
        db_session.add(setting)
        db_session.commit()
        setting_id = setting.id
        db_session.expunge_all()

        loaded = db_session.get(DomainSetting, setting_id)
        assert loaded.value_text == "live_sk_supersecret"  # decrypted on load

        # Touch something unrelated and flush.
        loaded.is_active = True
        db_session.commit()

        assert _raw_value(db_session, setting_id).startswith("enc:")

    def test_history_never_stores_the_secret_value(self, db_session, key_env):
        """The audit trail kept a second plaintext copy of every secret — one
        per change — which outlived even a rotation of the live setting.

        It records *that* a value changed and *who* changed it. It never needed
        the value.
        """
        from app.models.domain_settings import SettingChangeAction
        from app.services.domain_settings import _record_setting_history

        setting = DomainSetting(
            domain=SettingDomain.banking,
            key="mono_secret_key",
            value_type=SettingValueType.string,
            value_text="live_sk_supersecret",
            is_secret=True,
        )
        db_session.add(setting)
        db_session.flush()

        _record_setting_history(
            db_session,
            setting,
            SettingChangeAction.UPDATE,
            old_value_text="live_sk_previous",
            old_is_secret=True,
        )
        db_session.commit()

        # Scope to the row this test wrote — the table is shared across the
        # session and other tests leave history behind.
        row = db_session.execute(
            text(
                "SELECT old_value_text, new_value_text, key "
                "FROM domain_setting_history WHERE setting_id = :sid"
            ),
            {"sid": str(setting.id)},
        ).one()

        assert row.key == "mono_secret_key"  # the trail still names the setting
        assert "live_sk_previous" not in (row.old_value_text or "")
        assert "live_sk_supersecret" not in (row.new_value_text or "")
        assert row.old_value_text == "***MASKED***"
        assert row.new_value_text == "***MASKED***"

    def test_rotating_a_secret_re_encrypts(self, db_session, key_env):
        setting = DomainSetting(
            domain=SettingDomain.banking,
            key="mono_secret_key",
            value_type=SettingValueType.string,
            value_text="live_sk_old",
            is_secret=True,
        )
        db_session.add(setting)
        db_session.commit()

        setting.value_text = "live_sk_rotated"
        db_session.commit()
        setting_id = setting.id
        db_session.expunge_all()

        stored = _raw_value(db_session, setting_id)
        assert stored.startswith("enc:")
        assert "live_sk_rotated" not in stored
        assert db_session.get(DomainSetting, setting_id).value_text == "live_sk_rotated"
