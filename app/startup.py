"""Startup validation for production readiness.

This module validates that all required configuration is present before
the application starts accepting requests. This prevents silent failures
where the app starts but crashes on first authentication attempt.
"""

import logging
import os
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


class StartupValidationError(Exception):
    """Raised when startup validation fails."""

    pass


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
    """Validate that required secrets are configured.

    Supports both direct values and OpenBao references (bao://, openbao://, vault://).
    Returns a list of error messages for any missing secrets.
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

    return errors


def validate_openbao_connectivity() -> list[str]:
    """Validate OpenBao connectivity if any secrets reference it.

    This is a soft check - only validates if OpenBao references are used.
    """
    errors: list[str] = []

    # Check if any required secrets use OpenBao
    uses_openbao = any(
        is_openbao_ref(os.getenv(name) or "")
        for name, _ in REQUIRED_SECRETS
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
