"""Versioned event registry for the dotmac_academy webhook.

Dispatch is a table lookup on ``(version, event)`` rather than an if-chain, so
a future breaking payload ships as version 2 beside version 1 instead of
reshaping the handler under the existing callers.

Results are transport-neutral status dicts. The route translates them to HTTP —
services here never import FastAPI types.
"""

from __future__ import annotations

import logging
from typing import Any
from collections.abc import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.dotmac_academy.training_sync import record_course_completion

logger = logging.getLogger(__name__)

# The version assumed when a payload omits one. The academy did not send a
# version before 2026-08; treating those as v1 keeps older senders working.
DEFAULT_VERSION = 1

Handler = Callable[..., dict[str, Any]]

_HANDLERS: dict[tuple[int, str], Handler] = {
    (1, "course_completed"): record_course_completion,
}

SUPPORTED_EVENTS = sorted({event for _, event in _HANDLERS})


def _coerce_version(raw: Any) -> int | None:
    if raw is None:
        return DEFAULT_VERSION
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def dispatch(
    db: Session, *, organization_id: UUID, event_type: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Route one webhook payload to its handler.

    Returns the handler's status dict, or an ``unsupported`` status naming what
    was received — an unroutable event must say so rather than be absorbed.
    """
    version = _coerce_version(payload.get("version"))
    if version is None:
        return {
            "status": "unsupported",
            "reason": f"unreadable version {payload.get('version')!r}",
        }

    handler = _HANDLERS.get((version, event_type))
    if handler is None:
        logger.warning(
            "dotmac_academy: no handler for version=%s event=%s", version, event_type
        )
        return {
            "status": "unsupported",
            "reason": f"no handler for version {version} event {event_type!r}",
        }

    return handler(db, organization_id=organization_id, payload=payload)
