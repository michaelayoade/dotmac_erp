"""Pure ERP boot-secret policy shared by startup and candidate readiness.

No application startup, database, or runtime secret resolution runs here.
"""

import base64
import logging
import os
import re

from app.services.secrets import is_openbao_ref

logger = logging.getLogger("app.startup")

# Minimum entropy requirements (in bytes)
MIN_JWT_SECRET_BYTES = 32  # 256 bits minimum for HS256
MIN_TOTP_KEY_BYTES = 32  # Fernet key must be 32 bytes base64-encoded

# Placeholder secrets that should never be used in production
WEAK_SECRET_PATTERNS = [
    r"^changeme$",
    r"^secret$",
    r"^password$",
    r"^test$",
    r"^dev$",
    r"^development$",
    r"^placeholder$",
    r"^replace[-_]?me$",
    r"^your[-_]?secret[-_]?here$",
    r"^xxx+$",
    r"^12345",
    r"^abcdef",
]


def _estimate_entropy_bytes(secret: str) -> int:
    """Estimate the entropy of a secret in bytes.

    This is a conservative estimate based on the character set used.
    For base64-encoded secrets, we estimate the raw byte length.
    """
    # Check if it looks like base64 (common for generated secrets)
    if re.match(r"^[A-Za-z0-9+/=_-]+$", secret):
        # URL-safe base64 or standard base64
        try:
            # Try to decode to get actual byte length
            decoded = base64.urlsafe_b64decode(secret + "==")
            return len(decoded)
        except Exception:
            try:
                decoded = base64.b64decode(secret + "==")
                return len(decoded)
            except Exception:
                logger.exception("Ignored exception")

    # For non-base64 secrets, estimate based on character set
    has_upper = bool(re.search(r"[A-Z]", secret))
    has_lower = bool(re.search(r"[a-z]", secret))
    has_digit = bool(re.search(r"[0-9]", secret))
    has_special = bool(re.search(r"[^A-Za-z0-9]", secret))

    # Calculate bits per character based on character set
    charset_size = 0
    if has_upper:
        charset_size += 26
    if has_lower:
        charset_size += 26
    if has_digit:
        charset_size += 10
    if has_special:
        charset_size += 32  # Conservative estimate for special chars

    if charset_size == 0:
        return 0

    import math

    bits_per_char = math.log2(charset_size)
    total_bits = len(secret) * bits_per_char
    return int(total_bits / 8)


def _is_weak_secret(secret: str) -> bool:
    """Check if a secret matches known weak patterns."""
    secret_lower = secret.lower().strip()
    return any(re.match(pattern, secret_lower) for pattern in WEAK_SECRET_PATTERNS)


def _validate_fernet_key(key: str) -> tuple[bool, str]:
    """Validate that a key is a valid Fernet key (32 bytes, base64-encoded).

    Returns (is_valid, error_message).
    """
    try:
        # Fernet expects 32 bytes, URL-safe base64 encoded
        decoded = base64.urlsafe_b64decode(key)
        if len(decoded) != 32:
            return (
                False,
                f"TOTP_ENCRYPTION_KEY must be exactly 32 bytes when decoded (got {len(decoded)} bytes)",
            )
        return True, ""
    except Exception as e:
        return False, f"TOTP_ENCRYPTION_KEY is not valid URL-safe base64: {e}"


def validate_secret_value(name: str, value: str) -> list[str]:
    """Apply ERP startup format policy to one already-resolved required secret."""
    errors: list[str] = []
    # Check for weak/placeholder secrets
    if _is_weak_secret(value):
        errors.append(
            f"SECURITY: {name} appears to be a placeholder or weak secret. "
            f"Please generate a cryptographically secure random value."
        )
        return errors

    # Validate specific secret formats
    if name == "JWT_SECRET":
        entropy_bytes = _estimate_entropy_bytes(value)
        if entropy_bytes < MIN_JWT_SECRET_BYTES:
            errors.append(
                f"SECURITY: {name} has insufficient entropy (~{entropy_bytes} bytes). "
                f"Minimum required: {MIN_JWT_SECRET_BYTES} bytes (256 bits). "
                f'Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )
    elif name == "TOTP_ENCRYPTION_KEY":
        is_valid, error_msg = _validate_fernet_key(value)
        if not is_valid:
            errors.append(
                f'SECURITY: {error_msg}. Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )
    return errors


# Required secrets that must be configured for the app to function
REQUIRED_SECRETS = [
    ("JWT_SECRET", "Required for signing authentication tokens"),
    ("TOTP_ENCRYPTION_KEY", "Required for encrypting MFA secrets"),
]

# Required for database connectivity
REQUIRED_CONFIG = [
    ("DATABASE_URL", "Required for database connectivity"),
]


def validate_required_config() -> list[str]:
    """Validate that required configuration is present.

    Returns a list of error messages for any missing configuration.
    """
    errors: list[str] = []

    for name, description in REQUIRED_CONFIG:
        value = os.getenv(name)
        if not value:
            errors.append(f"Missing {name}: {description}")

    return errors


def validate_openbao_connectivity() -> list[str]:
    """Validate OpenBao connectivity if any secrets reference it.

    This is a soft check - only validates if OpenBao references are used.
    """
    errors: list[str] = []

    # Check if any required secrets use OpenBao
    uses_openbao = any(
        is_openbao_ref(os.getenv(name) or "") for name, _ in REQUIRED_SECRETS
    )

    if not uses_openbao:
        return errors

    # Validate OpenBao configuration
    addr = os.getenv("OPENBAO_ADDR") or os.getenv("VAULT_ADDR")
    token = os.getenv("OPENBAO_TOKEN") or os.getenv("VAULT_TOKEN")

    if not addr:
        errors.append(
            "OpenBao address not configured (OPENBAO_ADDR or VAULT_ADDR) "
            "but secrets reference OpenBao"
        )
    if not token:
        errors.append(
            "OpenBao token not configured (OPENBAO_TOKEN or VAULT_TOKEN) "
            "but secrets reference OpenBao"
        )

    return errors
