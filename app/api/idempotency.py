"""
API idempotency helpers.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse, Response

from app.models.finance.platform.idempotency_record import IdempotencyRecord
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

    Returns a replay when the key already carries an outcome, or `None` when the
    caller now owns the operation and must execute it.

    A reservation whose lease has lapsed is taken over here rather than replayed
    forever. `reserve` writes its `202 "Request in progress"` placeholder BEFORE
    the side effect runs, so a request that died in between left a row that
    every retry replayed for the full 24h TTL — no lease, no stale detector, and
    no way to re-drive the work.
    """
    record = IdempotencyService.check(
        db=db,
        organization_id=organization_id,
        idempotency_key=idempotency_key,
        endpoint=endpoint,
        request_hash=request_hash,
    )

    if record is not None:
        if IdempotencyService.is_stale_reservation(
            record
        ) and IdempotencyService.take_over_reservation(db, record):
            # We won the take-over: execute for real. Losers fall through and
            # keep replaying the placeholder until the winner records an outcome.
            return None
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


def update_idempotency_response(
    db: Session,
    *,
    organization_id: UUID,
    idempotency_key: str,
    endpoint: str,
    response_status: int,
    response_body: dict[str, Any] | None,
) -> None:
    """Finalize a reservation through ERP's existing idempotency owner."""
    IdempotencyService.update_response(
        db=db,
        organization_id=organization_id,
        idempotency_key=idempotency_key,
        endpoint=endpoint,
        response_status=response_status,
        response_body=response_body,
    )


def release_idempotency_reservation(
    db: Session,
    *,
    organization_id: UUID,
    idempotency_key: str,
    endpoint: str,
    request_hash: str,
) -> None:
    """Release this caller's unfinished reservation after a failed operation."""
    record = IdempotencyService.check(
        db=db,
        organization_id=organization_id,
        idempotency_key=idempotency_key,
        endpoint=endpoint,
        request_hash=request_hash,
    )
    if (
        record is None
        or record.response_status != IdempotencyService.RESERVATION_STATUS
    ):
        return
    db.delete(record)
    db.commit()


def check_or_reserve_transactional_idempotency(
    db: Session,
    *,
    organization_id: UUID,
    idempotency_key: str,
    endpoint: str,
    request_hash: str,
) -> IdempotencyReplay | None:
    """Reserve inside the caller's transaction so cache and effect are atomic."""
    record = db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.organization_id == organization_id,
            IdempotencyRecord.idempotency_key == idempotency_key,
            IdempotencyRecord.endpoint == endpoint,
        )
    )
    if record is not None:
        now = datetime.now(timezone.utc)
        expires_at = record.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < now:
            db.delete(record)
            db.flush()
            record = None
        elif record.request_hash and record.request_hash != request_hash:
            raise HTTPException(
                status_code=409,
                detail="Idempotency key already used with different request body",
            )
        elif IdempotencyService.is_stale_reservation(record, now=now):
            record.created_at = now
            db.flush()
            return None
        else:
            return IdempotencyReplay(record.response_status, record.response_body)

    now = datetime.now(timezone.utc)
    reservation = IdempotencyRecord(
        organization_id=organization_id,
        idempotency_key=idempotency_key,
        endpoint=endpoint,
        request_hash=request_hash,
        response_status=IdempotencyService.RESERVATION_STATUS,
        response_body={"detail": "Request in progress"},
        expires_at=now + timedelta(hours=IdempotencyService.DEFAULT_TTL_HOURS),
    )
    if db.get_bind().dialect.name == "sqlite":
        db.add(reservation)
        db.flush()
        return None
    try:
        with db.begin_nested():
            db.add(reservation)
            db.flush()
    except IntegrityError:
        existing = db.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.organization_id == organization_id,
                IdempotencyRecord.idempotency_key == idempotency_key,
                IdempotencyRecord.endpoint == endpoint,
            )
        )
        if existing is None:
            raise
        if existing.request_hash and existing.request_hash != request_hash:
            raise HTTPException(
                status_code=409,
                detail="Idempotency key already used with different request body",
            )
        return IdempotencyReplay(existing.response_status, existing.response_body)
    return None


def update_transactional_idempotency_response(
    db: Session,
    *,
    organization_id: UUID,
    idempotency_key: str,
    endpoint: str,
    response_status: int,
    response_body: dict[str, Any] | None,
) -> None:
    """Stage a response without committing the caller's transaction."""
    record = db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.organization_id == organization_id,
            IdempotencyRecord.idempotency_key == idempotency_key,
            IdempotencyRecord.endpoint == endpoint,
        )
    )
    if record is None:
        raise RuntimeError("idempotency reservation disappeared")
    record.response_status = response_status
    record.response_body = response_body
    db.flush()


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
