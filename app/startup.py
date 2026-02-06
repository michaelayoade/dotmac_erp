"""Startup validation for production readiness.

This module validates that all required configuration is present before
the application starts accepting requests. This prevents silent failures
where the app starts but crashes on first authentication attempt.
"""

import base64
import logging
import os
import re
import sys

from sqlalchemy.orm import Session

from app.services.secrets import is_openbao_ref, resolve_openbao_ref

logger = logging.getLogger(__name__)

# Required secrets that must be configured for the app to function
REQUIRED_SECRETS = [
    ("JWT_SECRET", "Required for signing authentication tokens"),
    ("TOTP_ENCRYPTION_KEY", "Required for encrypting MFA secrets"),
]

# Required for database connectivity
REQUIRED_CONFIG = [
    ("DATABASE_URL", "Required for database connectivity"),
]

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


class StartupValidationError(Exception):
    """Raised when startup validation fails."""

    pass


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
                pass

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
    for pattern in WEAK_SECRET_PATTERNS:
        if re.match(pattern, secret_lower):
            return True
    return False


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


def _resolve_env_value(name: str, db: Session | None = None) -> str | None:
    """Resolve an environment variable, following OpenBao references if needed."""
    value = os.getenv(name)
    if not value:
        return None
    if is_openbao_ref(value):
        try:
            return resolve_openbao_ref(value, db)
        except Exception as e:
            logger.warning(f"Failed to resolve OpenBao reference for {name}: {e}")
            return None
    return value


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


def validate_required_secrets(db: Session | None = None) -> list[str]:
    """Validate that required secrets are configured with sufficient entropy.

    Supports both direct values and OpenBao references (bao://, openbao://, vault://).
    Returns a list of error messages for any missing or weak secrets.
    """
    errors: list[str] = []

    for name, description in REQUIRED_SECRETS:
        value = _resolve_env_value(name, db)
        if not value:
            raw = os.getenv(name)
            if raw and is_openbao_ref(raw):
                errors.append(
                    f"Missing {name}: OpenBao reference configured but failed to resolve. "
                    f"Check OPENBAO_ADDR and OPENBAO_TOKEN. ({description})"
                )
            else:
                errors.append(f"Missing {name}: {description}")
            continue

        # Check for weak/placeholder secrets
        if _is_weak_secret(value):
            errors.append(
                f"SECURITY: {name} appears to be a placeholder or weak secret. "
                f"Please generate a cryptographically secure random value."
            )
            continue

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


def validate_startup(db: Session | None = None, exit_on_failure: bool = True) -> bool:
    """Run all startup validations.

    Args:
        db: Optional database session for resolving OpenBao references
        exit_on_failure: If True, exit the process on validation failure

    Returns:
        True if all validations pass, False otherwise
    """
    all_errors: list[str] = []

    # Check basic config
    all_errors.extend(validate_required_config())

    # Check OpenBao connectivity (if used)
    all_errors.extend(validate_openbao_connectivity())

    # Check required secrets (with OpenBao resolution)
    all_errors.extend(validate_required_secrets(db))

    if all_errors:
        logger.error("=" * 60)
        logger.error("STARTUP VALIDATION FAILED")
        logger.error("=" * 60)
        for error in all_errors:
            logger.error(f"  - {error}")
        logger.error("=" * 60)
        logger.error("Application cannot start without required configuration.")
        logger.error("See documentation for setup instructions.")
        logger.error("=" * 60)

        if exit_on_failure:
            sys.exit(1)
        return False

    logger.info("Startup validation passed")
    return True


def log_startup_info():
    """Log non-sensitive startup information for debugging."""
    logger.info("=" * 60)
    logger.info("Dotmac ERP Starting")
    logger.info("=" * 60)

    # Log OpenBao configuration (without sensitive values)
    openbao_addr = os.getenv("OPENBAO_ADDR") or os.getenv("VAULT_ADDR")
    if openbao_addr:
        logger.info(f"OpenBao configured: {openbao_addr}")

    # Log which secrets use OpenBao
    for name, _ in REQUIRED_SECRETS:
        value = os.getenv(name)
        if value and is_openbao_ref(value):
            logger.info(f"{name}: Using OpenBao reference")
        elif value:
            logger.info(f"{name}: Using environment variable")
        else:
            logger.info(f"{name}: NOT CONFIGURED")

    logger.info("=" * 60)
