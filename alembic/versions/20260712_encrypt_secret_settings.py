"""Encrypt secret domain settings at rest; scrub secrets from settings history.

``domain_settings.value_text`` is a plain ``Text`` column, so every secret the
table holds — ``mono_secret_key``, ``paystack_secret_key``, ``jwt_secret``,
SMTP passwords — was stored in plaintext. ``is_secret`` was a display hint, not
a mask. The history table held a *second* plaintext copy of each, one per change,
which outlived even a rotation of the live value.

This migration:

1. Encrypts existing plaintext secret values in place, using the same Fernet key
   and ``enc:`` format as ``integration_config`` (``app/services/settings_crypto.py``).
2. Scrubs the secret values already recorded in ``domain_setting_history``,
   replacing them with a mask. The audit trail keeps *that* a value changed and
   *who* changed it — it never needed the value.

Deliberately tolerant, so a deploy cannot be bricked by key configuration:

* Values already encrypted (``enc:``) or held in OpenBao (``bao://``) are skipped.
* ``totp_encryption_key`` is skipped — on deployments that do not set the key in
  the environment, that row *is* the encryption key (``auth_flow._mfa_key``
  falls back to it). Encrypting it with itself is circular.
* If no key is configured at all, encryption is **skipped with a loud warning**
  rather than failing the migration. Reads keep working (decryption tolerates
  plaintext) and the values encrypt on their next write. The history scrub still
  runs — it needs no key.

Revision ID: 20260712_encrypt_secret_settings
Revises: 20260712_add_mono_last_ingest_at
Create Date: 2026-07-12
"""

from __future__ import annotations

import logging

import sqlalchemy as sa

from alembic import op

revision = "20260712_encrypt_secret_settings"
down_revision = "20260712_add_mono_last_ingest_at"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

MASKED_VALUE = "***MASKED***"
BOOTSTRAP_KEYS = ("totp_encryption_key",)


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Scrub plaintext secrets already sitting in the audit trail ────────
    # Runs regardless of key configuration — it needs no key, and this is where
    # the largest number of plaintext copies live (one per change, forever).
    scrubbed = conn.execute(
        sa.text(
            """
            UPDATE domain_setting_history
            SET old_value_text = CASE
                    WHEN old_is_secret AND old_value_text IS NOT NULL
                    THEN :mask ELSE old_value_text END,
                new_value_text = CASE
                    WHEN new_is_secret AND new_value_text IS NOT NULL
                    THEN :mask ELSE new_value_text END
            WHERE (old_is_secret AND old_value_text IS NOT NULL
                   AND old_value_text <> :mask)
               OR (new_is_secret AND new_value_text IS NOT NULL
                   AND new_value_text <> :mask)
            """
        ),
        {"mask": MASKED_VALUE},
    ).rowcount
    logger.info("Scrubbed secret values from %s settings-history rows", scrubbed)

    # ── 2. Encrypt plaintext secrets on the live settings ────────────────────
    from app.services.settings_crypto import _fernet
    from app.services.integration_config import ENCRYPTED_PREFIX
    from app.services.secrets import is_openbao_ref

    fernet = _fernet(None)  # env/OpenBao only — no ORM session inside a migration
    if fernet is None:
        logger.warning(
            "No settings encryption key configured (INTEGRATION_ENCRYPTION_KEY / "
            "TOTP_ENCRYPTION_KEY) — secret settings are left in PLAINTEXT. Reads "
            "still work; they will encrypt on their next write. Set a key and "
            "re-run this migration to encrypt them now."
        )
        return

    rows = conn.execute(
        sa.text(
            """
            SELECT id, key, value_text
            FROM domain_settings
            WHERE is_secret IS TRUE
              AND value_text IS NOT NULL
              AND value_text <> ''
            """
        )
    ).fetchall()

    encrypted = 0
    for row in rows:
        value = row.value_text
        if row.key in BOOTSTRAP_KEYS:
            continue
        if value.startswith(ENCRYPTED_PREFIX) or is_openbao_ref(value):
            continue

        ciphertext = f"{ENCRYPTED_PREFIX}{fernet.encrypt(value.encode()).decode()}"
        conn.execute(
            sa.text("UPDATE domain_settings SET value_text = :v WHERE id = :id"),
            {"v": ciphertext, "id": row.id},
        )
        encrypted += 1

    logger.info("Encrypted %s secret settings at rest", encrypted)


def downgrade() -> None:
    """Decrypt secret settings back to plaintext.

    The history scrub is NOT reversed — those plaintext values are gone, which
    is the point of this migration. Downgrade restores only the live settings,
    so the previous code (which cannot read ``enc:``) still works.
    """
    conn = op.get_bind()

    from app.services.settings_crypto import _fernet
    from app.services.integration_config import ENCRYPTED_PREFIX

    fernet = _fernet(None)
    if fernet is None:
        raise RuntimeError(
            "Cannot downgrade: no encryption key configured, so encrypted "
            "settings cannot be decrypted back to plaintext."
        )

    rows = conn.execute(
        sa.text(
            """
            SELECT id, value_text
            FROM domain_settings
            WHERE is_secret IS TRUE AND value_text LIKE 'enc:%'
            """
        )
    ).fetchall()

    for row in rows:
        plaintext = fernet.decrypt(
            row.value_text[len(ENCRYPTED_PREFIX) :].encode()
        ).decode()
        conn.execute(
            sa.text("UPDATE domain_settings SET value_text = :v WHERE id = :id"),
            {"v": plaintext, "id": row.id},
        )
