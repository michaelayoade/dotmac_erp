"""
Event Dispatcher for Workflow Automation.

Provides ``fire_workflow_event()`` — the single entry point that all
service-layer status-transition methods call after a state change.

Usage::

    try:
        from app.services.finance.automation.event_dispatcher import fire_workflow_event
        fire_workflow_event(
            db=self.db,
            organization_id=org_id,
            entity_type="EXPENSE",
            entity_id=claim.claim_id,
            event="ON_APPROVAL",
            old_values={"status": "SUBMITTED"},
            new_values={"status": "APPROVED"},
            user_id=approver_id,
        )
    except Exception:
        pass  # Side effect — never breaks the main operation
"""

import logging
from typing import Any, Dict, List, Optional
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
    old_values: Optional[Dict[str, Any]] = None,
    new_values: Optional[Dict[str, Any]] = None,
    changed_fields: Optional[List[str]] = None,
    user_id: Optional[UUID] = None,
) -> None:
    """Fire a workflow event, matching and executing any applicable rules.

    This function is intentionally **fire-and-forget**: callers should
    wrap it in ``try/except Exception: pass`` so that workflow failures
    never break the primary business operation.

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
        return

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

    workflow_service.trigger_event(db, organization_id, context)
