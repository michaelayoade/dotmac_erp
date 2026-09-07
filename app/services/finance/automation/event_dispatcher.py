"""
Event Dispatcher for Workflow Automation.

Provides ``fire_workflow_event()`` — the single entry point that service-layer
writers call after a state change. Async actions are stored in the platform
outbox in the same transaction. Validation and blocking actions execute
synchronously and must not be swallowed by callers.
"""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def fire_workflow_event(
    db: Session,
    organization_id: UUID,
    entity_type: str,
    entity_id: UUID,
    event: str,
    *,
    old_values: dict[str, Any] | None = None,
    new_values: dict[str, Any] | None = None,
    changed_fields: list[str] | None = None,
    user_id: UUID | None = None,
) -> list[Any]:
    """Fire a workflow event, matching and executing any applicable rules.

    Async actions are persisted in the caller's transaction through the
    platform outbox. Validation and blocking actions run synchronously and may
    reject the caller's operation.

    Args:
        db: Active database session (same session as the calling service).
        organization_id: Tenant scope.
        entity_type: Upper-case entity type string matching
            ``WorkflowEntityType`` (e.g. "EXPENSE", "INVOICE").
        entity_id: Primary key of the entity that changed.
        event: Trigger event string matching ``TriggerEvent``
            (e.g. "ON_APPROVAL", "ON_STATUS_CHANGE").
        old_values: Dict of field values *before* the change.
        new_values: Dict of field values *after* the change.
        changed_fields: List of field names that changed.
        user_id: The user who triggered the event.
    """
    from app.models.finance.automation import TriggerEvent
    from app.services.finance.automation.workflow import (
        TriggerContext,
        workflow_service,
    )

    try:
        trigger_event = TriggerEvent(event)
    except ValueError:
        logger.debug("Unknown trigger event '%s', skipping", event)
        return []

    # Module emitters and the automatic ORM connector share this de-duplication
    # set, so an explicit semantic event is not fired twice at commit time.
    fired = db.info.setdefault("automation_fired_events", set())
    fired.add((entity_type, str(entity_id), trigger_event.value))

    effective_new = dict(new_values or {})
    effective_old = dict(old_values or {})
    try:
        from app.models.finance.automation import CustomFieldEntityType
        from app.services.finance.automation.custom_fields import custom_fields_service

        custom_entity_type = CustomFieldEntityType(entity_type)
        custom_values = custom_fields_service.get_values(
            db, organization_id, custom_entity_type, entity_id
        )
        effective_new["custom_fields"] = custom_values
        effective_old.setdefault("custom_fields", custom_values)
    except ValueError:
        pass

    context = TriggerContext(
        entity_type=entity_type,
        entity_id=entity_id,
        event=trigger_event,
        organization_id=organization_id,
        old_values=effective_old,
        new_values=effective_new,
        changed_fields=changed_fields,
        user_id=user_id,
    )

    return workflow_service.trigger_event(db, organization_id, context)
