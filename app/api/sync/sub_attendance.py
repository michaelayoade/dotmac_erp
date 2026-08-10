"""Service-to-service Selfcare attendance API."""

from __future__ import annotations

import hashlib
import time
from typing import Any, cast
from uuid import UUID

import redis
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse, Response

from app.api.idempotency import (
    build_cached_response,
    build_request_hash,
    check_or_reserve_idempotency,
    require_idempotency_key,
)
from app.api.service_principal import (
    get_db_with_service_org,
    require_explicit_service_scope,
)
from app.schemas.sync.sub_attendance import (
    SelfcareAttendanceLocation,
    SelfcareAttendanceRead,
)
from app.services.finance.platform.idempotency import IdempotencyService
from app.services.cache import get_redis_client
from app.services.people.attendance.selfcare_integration import (
    SelfcareAttendanceError,
    SelfcareAttendanceIntegrationService,
)

router = APIRouter(prefix="/sync/sub/attendance", tags=["sub-attendance"])
_PUNCH_IDEMPOTENCY_ENDPOINT = "sub-attendance-punch"


def _enforce_punch_rate_limit(auth: dict, subject: UUID, idempotency_key: str) -> None:
    """Limit punches per service principal and subject before any mutation."""
    client = get_redis_client()
    if client is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "attendance_unavailable",
                "message": "Attendance service is temporarily unavailable.",
            },
        )
    window_seconds = 60
    window = int(time.time() // window_seconds)
    key = f"attendance-punch:{auth['api_key_id']}:{subject}:{window}"
    request_fingerprint = hashlib.sha256(idempotency_key.encode()).hexdigest()
    seen_key = (
        f"attendance-punch-seen:{auth['api_key_id']}:{subject}:{request_fingerprint}"
    )
    try:
        first_attempt = client.set(seen_key, "1", nx=True, ex=window_seconds)
        if not first_attempt:
            return
        count = int(cast(Any, client.incr(key)))
        if count == 1:
            client.expire(key, window_seconds)
    except redis.RedisError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "attendance_unavailable",
                "message": "Attendance service is temporarily unavailable.",
            },
        ) from exc
    if count > 30:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "rate_limited",
                "message": "Too many attendance attempts. Please try again shortly.",
            },
        )


def _subject(value: str) -> UUID:
    try:
        return UUID(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "authorization_failed",
                "message": "Invalid Selfcare subject.",
            },
        ) from exc


@router.get("/today", response_model=SelfcareAttendanceRead)
def today(
    x_selfcare_subject: str = Header(..., alias="X-Selfcare-Subject"),
    auth: dict = Depends(require_explicit_service_scope("sub:attendance:read")),
    db: Session = Depends(get_db_with_service_org),
) -> SelfcareAttendanceRead:
    try:
        return SelfcareAttendanceIntegrationService(db).today(
            auth["organization_id"], _subject(x_selfcare_subject)
        )
    except SelfcareAttendanceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


def _punch(
    action: str,
    payload: SelfcareAttendanceLocation,
    x_selfcare_subject: str,
    idempotency_key: str | None,
    request_id: str | None,
    auth: dict,
    db: Session,
) -> SelfcareAttendanceRead | Response:
    key = require_idempotency_key(idempotency_key)
    subject = _subject(x_selfcare_subject)
    _enforce_punch_rate_limit(auth, subject, key)
    organization_id = auth["organization_id"]
    request_hash = build_request_hash(
        payload,
        extra={"action": action, "subject": str(subject)},
    )
    replay = check_or_reserve_idempotency(
        db,
        organization_id=organization_id,
        idempotency_key=key,
        endpoint=_PUNCH_IDEMPOTENCY_ENDPOINT,
        request_hash=request_hash,
    )
    if replay is not None:
        return build_cached_response(replay)

    service = SelfcareAttendanceIntegrationService(db)
    try:
        handler = service.check_in if action == "check_in" else service.check_out
        state = handler(
            organization_id,
            subject,
            payload,
            request_id=request_id,
            service_person_id=auth.get("person_id"),
        )
    except SelfcareAttendanceError as exc:
        body: dict[str, Any] = {"detail": {"code": exc.code, "message": exc.message}}
        IdempotencyService.update_response(
            db,
            organization_id,
            key,
            _PUNCH_IDEMPOTENCY_ENDPOINT,
            exc.status_code,
            body,
        )
        return JSONResponse(status_code=exc.status_code, content=body)

    body = state.model_dump(mode="json")
    IdempotencyService.update_response(
        db,
        organization_id,
        key,
        _PUNCH_IDEMPOTENCY_ENDPOINT,
        200,
        body,
    )
    return state


@router.post("/check-in", response_model=SelfcareAttendanceRead)
def check_in(
    payload: SelfcareAttendanceLocation,
    x_selfcare_subject: str = Header(..., alias="X-Selfcare-Subject"),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", max_length=200
    ),
    request_id: str | None = Header(default=None, alias="X-Request-ID", max_length=100),
    auth: dict = Depends(require_explicit_service_scope("sub:attendance:write")),
    db: Session = Depends(get_db_with_service_org),
):
    return _punch(
        "check_in",
        payload,
        x_selfcare_subject,
        idempotency_key,
        request_id,
        auth,
        db,
    )


@router.post("/check-out", response_model=SelfcareAttendanceRead)
def check_out(
    payload: SelfcareAttendanceLocation,
    x_selfcare_subject: str = Header(..., alias="X-Selfcare-Subject"),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", max_length=200
    ),
    request_id: str | None = Header(default=None, alias="X-Request-ID", max_length=100),
    auth: dict = Depends(require_explicit_service_scope("sub:attendance:write")),
    db: Session = Depends(get_db_with_service_org),
):
    return _punch(
        "check_out",
        payload,
        x_selfcare_subject,
        idempotency_key,
        request_id,
        auth,
        db,
    )
