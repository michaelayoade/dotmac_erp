"""At-rest encryption for secret domain settings.

``DomainSetting.value_text`` is a plain ``Text`` column, so every secret the
settings table holds — payment-provider keys, ``jwt_secret``, SMTP passwords —
sat in the database in plaintext. ``is_secret`` was only a display hint.

Encryption is applied at the **ORM boundary** (see the mapper listeners in
``app/models/domain_settings.py``) rather than at each call site. There are
~170 references to ``value_text`` across the app; gating each one would
guarantee that some read path eventually gets missed and hands ciphertext to a
live API call. At the boundary, a secret is encrypted whenever it is written
and decrypted whenever it is loaded, and no caller has to know.

Format and key are shared with ``integration_config`` — same ``enc:`` prefix,
same Fernet key, same OpenBao (``bao://``) support — so there is one scheme
here, not two.

Three properties keep this safe to roll out:

* **Decryption is tolerant.** Plaintext (legacy), ``enc:`` ciphertext, and
  ``bao://`` references all resolve. Existing rows keep working before the
  backfill migration runs.
* **Encryption is idempotent.** Already-encrypted values and OpenBao
  references pass through untouched, so a re-flush cannot double-encrypt.
* **A missing key never breaks the app.** It logs loudly and stores plaintext
  — the status quo — rather than making settings unsavable on a deployment
  that has no key configured.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from cryptography.fernet import Fernet, InvalidToken

from app.services.integration_config import ENCRYPTED_PREFIX
from app.services.secrets import is_openbao_ref, resolve_secret

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.orm import Session

    from app.models.domain_settings import DomainSetting

logger = logging.getLogger(__name__)


# ``totp_encryption_key`` IS the encryption key on deployments that do not set
# it in the environment — ``auth_flow._mfa_key`` falls back to reading this row
# out of the settings table. Encrypting it with itself is circular: the value
# needed to decrypt it would only be recoverable by decrypting it. It stays in
# plaintext, and belongs in the environment or OpenBao rather than the DB.
BOOTSTRAP_KEYS = frozenset({"totp_encryption_key"})


def is_bootstrap_key(key: str | None) -> bool:
    """Is this the key that everything else is encrypted with?"""
    return key in BOOTSTRAP_KEYS


def should_encrypt(setting: DomainSetting) -> bool:
    """Should this setting's value be encrypted at rest?"""
    return bool(getattr(setting, "is_secret", False)) and not is_bootstrap_key(
        getattr(setting, "key", None)
    )


def _raw_key(db: Session | None) -> str | None:
    """Resolve the Fernet key: environment first, then the bootstrap setting.

    Mirrors ``auth_flow._mfa_key``. The DB fallback matters — a deployment
    whose MFA works off the stored ``totp_encryption_key`` has no env var set,
    and must not suddenly be unable to write secrets.
    """
    key = os.getenv("INTEGRATION_ENCRYPTION_KEY") or os.getenv("TOTP_ENCRYPTION_KEY")
    if key:
        return key

    if db is None:
        return None

    # Read the bootstrap row directly. It is never encrypted (see BOOTSTRAP_KEYS),
    # so this cannot recurse back into decryption.
    from sqlalchemy import select

    from app.models.domain_settings import DomainSetting, SettingDomain

    try:
        with db.no_autoflush:
            return db.scalar(
                select(DomainSetting.value_text)
                .where(DomainSetting.domain == SettingDomain.auth)
                .where(DomainSetting.key == "totp_encryption_key")
                .where(DomainSetting.is_active.is_(True))
            )
    except Exception:  # pragma: no cover — never let key lookup break a load
        logger.exception("Could not read the bootstrap encryption key")
        return None


def _fernet(db: Session | None) -> Fernet | None:
    raw = _raw_key(db)
    if not raw:
        return None
    resolved = resolve_secret(raw, db) if is_openbao_ref(raw) else raw
    if not resolved:
        return None
    try:
        return Fernet(resolved.encode())
    except (ValueError, TypeError):
        logger.error(
            "Settings encryption key is not a valid Fernet key; "
            "secrets cannot be encrypted at rest"
        )
        return None


def encrypt_value(value: str | None, db: Session | None = None) -> str | None:
    """Encrypt a secret setting's value for storage. Idempotent.

    Passes through anything already encrypted or held in OpenBao. Falls back to
    plaintext — loudly — when no key is configured, rather than making the
    setting unsavable.
    """
    if not value:
        return value
    if value.startswith(ENCRYPTED_PREFIX) or is_openbao_ref(value):
        return value

    fernet = _fernet(db)
    if fernet is None:
        logger.error(
            "No settings encryption key configured — storing a secret setting "
            "in PLAINTEXT. Set INTEGRATION_ENCRYPTION_KEY (or "
            "TOTP_ENCRYPTION_KEY) to encrypt secrets at rest."
        )
        return value

    return f"{ENCRYPTED_PREFIX}{fernet.encrypt(value.encode()).decode()}"


def decrypt_value(value: str | None, db: Session | None = None) -> str | None:
    """Decrypt a stored secret. Tolerates plaintext and OpenBao references."""
    if not value:
        return value

    if is_openbao_ref(value):
        return resolve_secret(value, db)

    if not value.startswith(ENCRYPTED_PREFIX):
        # Legacy plaintext, written before this module existed or on a
        # deployment with no key. Readable as-is; the backfill migration and
        # the next write both convert it.
        return value

    fernet = _fernet(db)
    if fernet is None:
        logger.error(
            "A settings value is encrypted but no key is configured — cannot "
            "decrypt. Set INTEGRATION_ENCRYPTION_KEY or TOTP_ENCRYPTION_KEY."
        )
        return None

    try:
        return fernet.decrypt(value[len(ENCRYPTED_PREFIX) :].encode()).decode()
    except InvalidToken:
        logger.error(
            "Could not decrypt a settings value — wrong encryption key, or the "
            "value was encrypted under a key that has since been rotated."
        )
        return None
