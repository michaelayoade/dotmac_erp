"""
API idempotency helpers.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse, Response

from app.services.finance.platform.idempotency import IdempotencyService


@dataclass
class IdempotencyReplay:
    status_code: int
    body: dict[str, Any] | None


def _normalize_payload(payload: Any) -> Any:
    if payload is None:
        return None
    if hasattr(payload, "model_dump"):
        return payload.model_dump(mode="json")
    return payload


def build_request_hash(payload: Any, extra: dict[str, Any] | None = None) -> str:
    """Build a stable SHA256 hash for request idempotency."""
    normalized = {
        "payload": _normalize_payload(payload),
        "extra": extra or {},
    }
    raw = json.dumps(normalized, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def check_or_reserve_idempotency(
    db: Session,
    *,
    organization_id: UUID,
    idempotency_key: str,
    endpoint: str,
    request_hash: str,
) -> IdempotencyReplay | None:
    """
    Check for an existing idempotency record, or reserve a new one.
    """
    record = IdempotencyService.check(
        db=db,
        organization_id=organization_id,
        idempotency_key=idempotency_key,
        endpoint=endpoint,
        request_hash=request_hash,
    )

    if record is not None:
        return IdempotencyReplay(
            status_code=record.response_status,
            body=record.response_body,
        )

    IdempotencyService.reserve(
        db=db,
        organization_id=organization_id,
        idempotency_key=idempotency_key,
        endpoint=endpoint,
        request_hash=request_hash,
    )
    return None


def build_cached_response(replay: IdempotencyReplay) -> Response:
    """Return a response from cached idempotency data."""
    if replay.body is None:
        return Response(status_code=replay.status_code)
    return JSONResponse(status_code=replay.status_code, content=replay.body)


def require_idempotency_key(idempotency_key: str | None) -> str:
    if not idempotency_key:
        raise HTTPException(
            status_code=400, detail="Idempotency-Key header is required"
        )
    return idempotency_key
