"""Hardware fingerprint collection.

Generates a deterministic machine identifier for hardware-locked licenses.
Reads ``/etc/machine-id`` when available, falls back to a hash of the MAC
address and hostname.
"""

from __future__ import annotations

import hashlib
import logging
import platform
import uuid

logger = logging.getLogger(__name__)

_CACHED_FINGERPRINT: str | None = None


def get_machine_fingerprint() -> str:
    """Return a stable ``sha256:<hex>`` fingerprint for the current machine.

    The result is cached for the lifetime of the process.
    """
    global _CACHED_FINGERPRINT
    if _CACHED_FINGERPRINT is not None:
        return _CACHED_FINGERPRINT

    raw = _read_machine_id() or _fallback_id()
    digest = hashlib.sha256(raw.encode()).hexdigest()
    _CACHED_FINGERPRINT = f"sha256:{digest}"
    logger.debug("Machine fingerprint: %s", _CACHED_FINGERPRINT)
    return _CACHED_FINGERPRINT


def _read_machine_id() -> str | None:
    """Try to read ``/etc/machine-id`` (Linux)."""
    try:
        with open("/etc/machine-id") as fh:
            value = fh.read().strip()
            if value:
                return value
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.debug("Could not read /etc/machine-id: %s", exc)
    return None


def _fallback_id() -> str:
    """Derive a fingerprint from MAC address + hostname."""
    mac = format(uuid.getnode(), "012x")
    hostname = platform.node()
    return f"{mac}:{hostname}"
