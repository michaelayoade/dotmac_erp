"""Audit service package.

Exports the legacy ``AuditEvents`` API at ``app.services.audit`` while also
hosting field-level tracking helpers in ``app.services.audit.field_tracker``.
"""

from __future__ import annotations

import logging

from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditActorType, AuditEvent
from app.schemas.audit import AuditEventCreate
from app.services.common import NotFoundError, coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


def _apply_ordering(query, order_by: str, order_dir: str, allowed_columns: dict):
    if order_by not in allowed_columns:
        raise ValueError(
            f"Invalid order_by. Allowed: {', '.join(sorted(allowed_columns))}"
        )
    column = allowed_columns[order_by]
    if order_dir == "desc":
        return query.order_by(column.desc())
    return query.order_by(column.asc())


def _apply_pagination(query, limit: int, offset: int):
    return query.limit(limit).offset(offset)


class AuditEvents(ListResponseMixin):
    @staticmethod
    def parse_actor_type(value: str | None) -> AuditActorType | None:
        if value is None:
            return None
        try:
            return AuditActorType(value)
        except ValueError as exc:
            allowed = ", ".join(sorted(a.value for a in AuditActorType))
            raise ValueError(
                f"Invalid actor_type. Allowed: {allowed}",
            ) from exc

    @staticmethod
    def create(db: Session, payload: AuditEventCreate) -> AuditEvent:
        data = payload.model_dump()
        if payload.occurred_at is None:
            data.pop("occurred_at", None)
        event = AuditEvent(**data)
        db.add(event)
        db.flush()
        db.refresh(event)
        return event

    @staticmethod
    def get(db: Session, event_id: str) -> AuditEvent:
        event = db.get(AuditEvent, coerce_uuid(event_id))
        if not event:
            raise NotFoundError("Audit event not found")
        return event

    @staticmethod
    def list(
        db: Session,
        actor_id: str | None,
        actor_type: AuditActorType | None,
        action: str | None,
        entity_type: str | None,
        request_id: str | None,
        is_success: bool | None,
        status_code: int | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> list[AuditEvent]:
        allowed_columns = {
            "occurred_at": AuditEvent.occurred_at,
            "action": AuditEvent.action,
            "entity_type": AuditEvent.entity_type,
            "status_code": AuditEvent.status_code,
        }

        stmt = select(AuditEvent)
        if actor_id:
            stmt = stmt.where(AuditEvent.actor_id == actor_id)
        if actor_type:
            stmt = stmt.where(AuditEvent.actor_type == actor_type)
        if action:
            stmt = stmt.where(AuditEvent.action == action)
        if entity_type:
            stmt = stmt.where(AuditEvent.entity_type == entity_type)
        if request_id:
            stmt = stmt.where(AuditEvent.request_id == request_id)
        if is_success is not None:
            stmt = stmt.where(AuditEvent.is_success == is_success)
        if status_code is not None:
            stmt = stmt.where(AuditEvent.status_code == status_code)
        if is_active is None:
            stmt = stmt.where(AuditEvent.is_active.is_(True))
        else:
            stmt = stmt.where(AuditEvent.is_active == is_active)
        stmt = _apply_ordering(stmt, order_by, order_dir, allowed_columns)
        return list(db.scalars(_apply_pagination(stmt, limit, offset)))

    @staticmethod
    def log_request(db: Session, request: Request, response: Response) -> None:
        request_state = getattr(request, "state", None)
        state_actor_id = (
            getattr(request_state, "actor_id", None) if request_state else None
        )
        state_actor_type = (
            getattr(request_state, "actor_type", None) if request_state else None
        )
        state_request_id = (
            getattr(request_state, "request_id", None) if request_state else None
        )
        state_organization_id = (
            getattr(request_state, "organization_id", None) if request_state else None
        )

        actor_id = request.headers.get("x-actor-id") or state_actor_id
        if actor_id is not None:
            actor_id = str(actor_id)
        actor_person_id = None
        if actor_id:
            try:
                actor_person_id = coerce_uuid(actor_id, raise_http=False)
            except (TypeError, ValueError):
                actor_person_id = None

        actor_type = request.headers.get("x-actor-type")
        if not actor_type and state_actor_type:
            actor_type = str(state_actor_type)
        if not actor_type:
            actor_type = (
                AuditActorType.user.value if actor_id else AuditActorType.system.value
            )

        request_id = request.headers.get("x-request-id") or state_request_id
        organization_id = coerce_uuid(state_organization_id, raise_http=False)
        entity_id = request.headers.get("x-entity-id")
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        try:
            resolved_actor_type = AuditActorType(actor_type)
        except ValueError:
            resolved_actor_type = AuditActorType.system
        try:
            query_params = dict(request.query_params)
        except KeyError:
            query_params = {}
        payload = AuditEventCreate(
            actor_type=resolved_actor_type,
            organization_id=organization_id,
            actor_person_id=actor_person_id,
            actor_id=actor_id,
            action=request.method,
            entity_type=request.url.path,
            entity_id=entity_id,
            status_code=response.status_code,
            is_success=response.status_code < 400,
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
            metadata_={
                "path": request.url.path,
                "query": query_params,
            },
        )
        event = AuditEvent(**payload.model_dump())
        db.add(event)
        db.flush()

    @staticmethod
    def delete(db: Session, event_id: str) -> None:
        event = db.get(AuditEvent, coerce_uuid(event_id))
        if not event:
            raise NotFoundError("Audit event not found")
        event.is_active = False
        db.flush()


audit_events = AuditEvents()

__all__ = [
    "AuditActorType",
    "AuditEvent",
    "AuditEvents",
    "audit_events",
]
