"""Thread-safe singleton holding the current license state.

Provides a process-wide cache of the last license validation result so that
hot-path code (middleware, ``is_module_enabled()``) never touches the
filesystem or does crypto on every request.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.licensing.schema import LicensePayload, LicenseStatus

logger = logging.getLogger(__name__)


@dataclass
class LicenseState:
    """Snapshot of the most recent license validation."""

    status: LicenseStatus = LicenseStatus.MISSING
    payload: LicensePayload | None = None
    validated_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    error: str | None = None


_lock = threading.Lock()
_state = LicenseState()


def get_license_state() -> LicenseState:
    """Return a *snapshot* of the current license state (thread-safe read)."""
    with _lock:
        return _state


def set_license_state(new_state: LicenseState) -> None:
    """Replace the current license state (thread-safe write)."""
    global _state
    with _lock:
        _state = new_state
    logger.info(
        "License state updated: status=%s, validated_at=%s",
        new_state.status.value,
        new_state.validated_at.isoformat(),
    )
