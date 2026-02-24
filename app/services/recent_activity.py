"""
Recent activity helper for record-level audit trails.

Builds a normalized payload for templates from immutable audit log entries.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.inspection import inspect as sa_inspect
from sqlalchemy.orm import Session

from app.models.audit import AuditActorType, AuditEvent
from app.models.finance.audit.audit_log import AuditLog
from app.services.audit_info import get_audit_service
from app.services.common import coerce_uuid
from app.services.formatters import format_datetime

_ACTION_LABELS = {
    "INSERT": "Created",
    "UPDATE": "Updated",
    "DELETE": "Deleted",
}


def _resolve_record_id(record: Any) -> str | None:
    """Resolve primary key value for a mapped SQLAlchemy record."""
    try:
        mapper = sa_inspect(record.__class__)
    except Exception:
        return None

    if not mapper.primary_key:
        return None

    # Most entities in this app use a single UUID primary key.
    if len(mapper.primary_key) != 1:
        return None

    col = mapper.primary_key[0]
    value = getattr(record, col.key, None)
    return str(value) if value is not None else None


def get_recent_activity(
    db: Session,
    organization_id: UUID | str,
    *,
    table_schema: str,
    table_name: str,
    record_id: str,
    limit: int = 10,
) -> list[dict[str, str]]:
    """Fetch latest immutable audit entries for a single record."""
    org_id = coerce_uuid(organization_id)
    logs = list(
        db.scalars(
            select(AuditLog)
            .where(
                AuditLog.organization_id == org_id,
                AuditLog.table_schema == table_schema,
                AuditLog.table_name == table_name,
                AuditLog.record_id == record_id,
            )
            .order_by(AuditLog.occurred_at.desc())
            .limit(limit)
        ).all()
    )
    if not logs:
        return []

    audit_service = get_audit_service(db)
    resolved_actor_ids = {log.user_id for log in logs if log.user_id}
    correlation_ids = {
        log.correlation_id for log in logs if not log.user_id and log.correlation_id
    }
    actor_by_correlation: dict[str, UUID] = {}
    if correlation_ids:
        events = list(
            db.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.organization_id == org_id,
                    AuditEvent.request_id.in_(list(correlation_ids)),
                    AuditEvent.actor_type == AuditActorType.user,
                )
                .order_by(AuditEvent.occurred_at.desc())
            ).all()
        )
        for event in events:
            request_id = (event.request_id or "").strip()
            if not request_id or request_id in actor_by_correlation:
                continue
            actor_id = event.actor_person_id
            if actor_id is None and event.actor_id:
                parsed = coerce_uuid(event.actor_id, raise_http=False)
                actor_id = parsed if isinstance(parsed, UUID) else None
            if actor_id is not None:
                actor_by_correlation[request_id] = actor_id
                resolved_actor_ids.add(actor_id)

    user_names = audit_service.get_user_names_batch(list(resolved_actor_ids))

    items: list[dict[str, str]] = []
    for log in logs:
        action_raw = log.action.value if log.action else ""
        changed_fields = [field for field in (log.changed_fields or []) if field]
        shown_fields = ", ".join(changed_fields[:3])
        remaining = max(0, len(changed_fields) - 3)
        changed_fields_label = (
            f"{shown_fields} (+{remaining} more)"
            if remaining and shown_fields
            else shown_fields
        )

        actor_id = log.user_id
        if actor_id is None and log.correlation_id:
            actor_id = actor_by_correlation.get(log.correlation_id)
        user_name = user_names.get(actor_id) if actor_id else None
        actor_name = user_name or ("Unknown User" if actor_id else "System")
        items.append(
            {
                "audit_id": str(log.audit_id),
                "occurred_at": format_datetime(log.occurred_at),
                "action": action_raw,
                "action_label": _ACTION_LABELS.get(
                    action_raw, action_raw.title() if action_raw else "Updated"
                ),
                "actor_name": actor_name,
                "changed_fields_label": changed_fields_label,
                "reason": log.reason or "",
                "ip_address": log.ip_address or "",
                "correlation_id": log.correlation_id or "",
            }
        )
    return items


def get_recent_activity_for_record(
    db: Session,
    organization_id: UUID | str,
    *,
    record: Any | None,
    table_schema: str | None = None,
    table_name: str | None = None,
    record_id: str | None = None,
    limit: int = 10,
) -> list[dict[str, str]]:
    """
    Fetch latest immutable audit entries for a mapped record object.

    Schema/table/id can be explicitly overridden when needed.
    """
    if record is None:
        return []

    model_table = getattr(record, "__table__", None)
    resolved_schema = table_schema or getattr(model_table, "schema", None)
    resolved_table = table_name or getattr(model_table, "name", None)
    resolved_record_id = record_id or _resolve_record_id(record)

    if not resolved_schema or not resolved_table or not resolved_record_id:
        return []

    return get_recent_activity(
        db,
        organization_id,
        table_schema=resolved_schema,
        table_name=resolved_table,
        record_id=resolved_record_id,
        limit=limit,
    )
