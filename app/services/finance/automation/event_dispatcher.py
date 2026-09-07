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

    context = TriggerContext(
        entity_type=entity_type,
        entity_id=entity_id,
        event=trigger_event,
        organization_id=organization_id,
        old_values=old_values,
        new_values=new_values,
        changed_fields=changed_fields,
        user_id=user_id,
    )

    return workflow_service.trigger_event(db, organization_id, context)
