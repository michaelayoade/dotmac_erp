"""Automatic workflow events for every model in the entity registry."""

from __future__ import annotations

import enum
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from app.services.finance.automation.entity_registry import (
    entity_types_for_instance,
    get_pk_field,
)

logger = logging.getLogger(__name__)
_PENDING_KEY = "automation_pending_model_events"
_PROCESSING_KEY = "automation_processing_model_events"
_registered = False


def _json_value(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _snapshot(entity: Any) -> dict[str, Any]:
    state = inspect(entity)
    return {
        attr.key: _json_value(getattr(entity, attr.key, None))
        for attr in state.mapper.column_attrs
    }


def _collect_model_events(
    session: Session, _flush_context: Any, _instances: Any
) -> None:
    pending = session.info.setdefault(_PENDING_KEY, [])
    seen = {
        (
            id(item["entity"]),
            f"{item['entity_type']}:{item['event']}",
            tuple(item["changed_fields"]),
        )
        for item in pending
    }
    for collection, event_name in (
        (session.new, "ON_CREATE"),
        (session.dirty, "ON_UPDATE"),
        (session.deleted, "ON_DELETE"),
    ):
        for entity in collection:
            entity_types = entity_types_for_instance(entity)
            if not entity_types:
                continue
            org_id = getattr(entity, "organization_id", None)
            if org_id is None:
                continue
            values = _snapshot(entity)
            old_values = dict(values)
            changed_fields: list[str] = []
            if event_name == "ON_UPDATE":
                if not session.is_modified(entity, include_collections=False):
                    continue
                state = inspect(entity)
                for attr in state.mapper.column_attrs:
                    history = state.attrs[attr.key].history
                    if history.has_changes():
                        changed_fields.append(attr.key)
                        if history.deleted:
                            old_values[attr.key] = _json_value(history.deleted[0])
            for entity_type in entity_types:
                key = (id(entity), f"{entity_type}:{event_name}", tuple(changed_fields))
                if key in seen:
                    continue
                pending.append(
                    {
                        "entity": entity,
                        "entity_type": entity_type,
                        "organization_id": org_id,
                        "event": event_name,
                        "old_values": old_values if event_name != "ON_CREATE" else None,
                        "new_values": values if event_name != "ON_DELETE" else None,
                        "changed_fields": changed_fields,
                    }
                )
                seen.add(key)


def _derived_events(record: dict[str, Any]) -> list[str]:
    events = [record["event"]]
    if record["event"] != "ON_UPDATE":
        return events
    changed = record["changed_fields"]
    if changed:
        events.append("ON_FIELD_CHANGE")
        events.append("ON_THRESHOLD")
    if "status" in changed:
        events.append("ON_STATUS_CHANGE")
        status = str((record["new_values"] or {}).get("status", "")).upper()
        if status in {"APPROVED", "ACCEPTED", "AUTHORIZED"}:
            events.append("ON_APPROVAL")
        elif status in {"REJECTED", "DECLINED", "DENIED"}:
            events.append("ON_REJECTION")
        if status == "OVERDUE":
            events.append("ON_OVERDUE")
    return events


def _dispatch_before_commit(session: Session) -> None:
    if session.info.get(_PROCESSING_KEY):
        return
    if session.info.get("allow_cross_org") or not session.info.get("organization_id"):
        session.info.pop(_PENDING_KEY, None)
        return
    # Unit-test SQLite schemas intentionally omit most automation tables. The
    # production connector is a PostgreSQL/RLS transaction-boundary feature.
    if session.get_bind().dialect.name != "postgresql":
        session.info.pop(_PENDING_KEY, None)
        return
    session.info[_PROCESSING_KEY] = True
    try:
        session.flush()
        for _ in range(10):
            pending = session.info.pop(_PENDING_KEY, [])
            if not pending:
                break
            from app.models.finance.automation import WorkflowRule

            active_pairs = {
                (entity_type.value, trigger_event.value)
                for entity_type, trigger_event in session.execute(
                    select(WorkflowRule.entity_type, WorkflowRule.trigger_event).where(
                        WorkflowRule.organization_id == session.info["organization_id"],
                        WorkflowRule.is_active.is_(True),
                    )
                ).all()
            }
            if not active_pairs:
                continue
            fired = session.info.setdefault("automation_fired_events", set())
            for record in pending:
                if record["organization_id"] != session.info["organization_id"]:
                    continue
                entity_id = getattr(
                    record["entity"], get_pk_field(record["entity_type"]) or "", None
                )
                if entity_id is None:
                    continue
                for event_name in _derived_events(record):
                    if (record["entity_type"], event_name) not in active_pairs:
                        continue
                    signature = (record["entity_type"], str(entity_id), event_name)
                    if signature in fired:
                        continue
                    from app.services.finance.automation.event_dispatcher import (
                        fire_workflow_event,
                    )

                    fire_workflow_event(
                        session,
                        record["organization_id"],
                        record["entity_type"],
                        entity_id,
                        event_name,
                        old_values=record["old_values"],
                        new_values=record["new_values"],
                        changed_fields=record["changed_fields"],
                    )
            session.flush()
        else:
            raise RuntimeError("Workflow model-event cascade exceeded 10 cycles")
    finally:
        session.info[_PROCESSING_KEY] = False
        session.info.pop("automation_fired_events", None)


def register_automation_model_connectors() -> None:
    """Install the connector once in each web or worker process."""
    global _registered  # noqa: PLW0603
    if _registered:
        return
    from app.services.finance.automation.entity_configuration import (
        enforce_enabled_entity_writes,
    )

    event.listen(Session, "before_flush", enforce_enabled_entity_writes)
    event.listen(Session, "before_flush", _collect_model_events)
    event.listen(Session, "before_commit", _dispatch_before_commit)
    _registered = True
    logger.info("Automatic workflow model-event connectors registered")
