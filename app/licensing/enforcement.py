"""Enforcement hooks called at startup and at runtime.

``enforce_startup()`` is the main entry-point: it loads, validates, and
caches the license.  Helper functions expose the cached result so that
the rest of the application never repeats the crypto work.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.licensing.fingerprint import get_machine_fingerprint
from app.licensing.schema import LicensePayload, LicenseStatus
from app.licensing.state import LicenseState, get_license_state, set_license_state
from app.licensing.validator import load_license_file, verify_signature

logger = logging.getLogger(__name__)


def _is_dev_mode() -> bool:
    """Return ``True`` when license checks should be skipped entirely."""
    return os.getenv("DOTMAC_DEV_MODE", "true").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def enforce_startup(license_path: str | None = None) -> LicenseState:
    """Validate the license at application startup.

    * In dev mode the function returns immediately with ``DEV_MODE`` status.
    * On validation failure the process exits with code 1 **unless** the
      license is within its grace period.

    Returns the resulting :class:`LicenseState` (also cached via
    :func:`set_license_state`).
    """
    if _is_dev_mode():
        state = LicenseState(status=LicenseStatus.DEV_MODE)
        set_license_state(state)
        logger.info("License enforcement skipped (dev mode)")
        return state

    path = license_path or os.getenv("LICENSE_FILE_PATH") or "/app/license/dotmac.lic"

    state = validate_license(path)
    set_license_state(state)

    if state.status == LicenseStatus.EXPIRED:
        logger.error("=" * 60)
        logger.error("LICENSE EXPIRED — application cannot start")
        logger.error("License: %s", state.payload.license_id if state.payload else "?")
        logger.error("Error: %s", state.error or "past grace period")
        logger.error("=" * 60)
        sys.exit(1)

    if state.status == LicenseStatus.INVALID:
        logger.error("=" * 60)
        logger.error("LICENSE INVALID — application cannot start")
        logger.error("Error: %s", state.error or "signature verification failed")
        logger.error("=" * 60)
        sys.exit(1)

    if state.status == LicenseStatus.MISSING:
        logger.error("=" * 60)
        logger.error("LICENSE MISSING — application cannot start")
        logger.error("Expected at: %s", path)
        logger.error("=" * 60)
        sys.exit(1)

    if state.status == LicenseStatus.GRACE_PERIOD:
        logger.warning("=" * 60)
        logger.warning("LICENSE EXPIRED — running in GRACE PERIOD")
        if state.payload:
            logger.warning("Expires fully on: %s", _grace_deadline(state.payload))
        logger.warning("=" * 60)

    if state.status == LicenseStatus.EXPIRING_SOON:
        logger.warning(
            "License expiring soon: %s",
            state.payload.expires_at.isoformat() if state.payload else "?",
        )

    return state


def validate_license(path: str) -> LicenseState:
    """Full license validation without side-effects (no sys.exit).

    Used by both startup enforcement and the daily Celery re-check.
    """
    now = datetime.now(tz=UTC)

    if not Path(path).is_file():
        return LicenseState(
            status=LicenseStatus.MISSING, error=f"File not found: {path}"
        )

    try:
        lic = load_license_file(path)
    except (ValueError, OSError) as exc:
        return LicenseState(status=LicenseStatus.INVALID, error=str(exc))

    if not verify_signature(lic):
        return LicenseState(
            status=LicenseStatus.INVALID,
            payload=lic.payload,
            error="Signature verification failed",
        )

    payload = lic.payload

    # Hardware fingerprint check
    if payload.hardware_fingerprint_required and payload.hardware_fingerprint:
        machine_fp = get_machine_fingerprint()
        if machine_fp != payload.hardware_fingerprint:
            return LicenseState(
                status=LicenseStatus.INVALID,
                payload=payload,
                error=f"Hardware fingerprint mismatch: expected {payload.hardware_fingerprint}, got {machine_fp}",
            )

    # Expiry logic
    expires = (
        payload.expires_at.replace(tzinfo=UTC)
        if payload.expires_at.tzinfo is None
        else payload.expires_at
    )
    grace_end = expires + timedelta(days=payload.grace_period_days)

    if now > grace_end:
        return LicenseState(
            status=LicenseStatus.EXPIRED,
            payload=payload,
            error="License expired and grace period has ended",
        )

    if now > expires:
        return LicenseState(status=LicenseStatus.GRACE_PERIOD, payload=payload)

    days_left = (expires - now).days
    if days_left <= 30:
        return LicenseState(status=LicenseStatus.EXPIRING_SOON, payload=payload)

    return LicenseState(status=LicenseStatus.VALID, payload=payload)


# ---------------------------------------------------------------------------
# Runtime helpers (use the cached state — no I/O)
# ---------------------------------------------------------------------------


def get_licensed_modules() -> list[str] | None:
    """Return the list of licensed modules, or ``None`` if in dev mode."""
    state = get_license_state()
    if state.status == LicenseStatus.DEV_MODE:
        return None
    if state.payload is None:
        return None
    return state.payload.modules


def is_within_user_limit(active_users: int) -> bool:
    """Check whether the active user count is within the license limit."""
    state = get_license_state()
    if state.status == LicenseStatus.DEV_MODE:
        return True
    if state.payload is None:
        return False
    return active_users <= state.payload.max_users


def is_within_org_limit(org_count: int) -> bool:
    """Check whether the org count is within the license limit."""
    state = get_license_state()
    if state.status == LicenseStatus.DEV_MODE:
        return True
    if state.payload is None:
        return False
    return org_count <= state.payload.max_organizations


def is_in_grace_period() -> bool:
    """Return ``True`` when the license is expired but within grace."""
    return get_license_state().status == LicenseStatus.GRACE_PERIOD


def _grace_deadline(payload: LicensePayload) -> str:
    """Human-readable date when the grace period ends."""
    expires = (
        payload.expires_at.replace(tzinfo=UTC)
        if payload.expires_at.tzinfo is None
        else payload.expires_at
    )
    return str(
        (expires + timedelta(days=payload.grace_period_days)).strftime("%Y-%m-%d")
    )
