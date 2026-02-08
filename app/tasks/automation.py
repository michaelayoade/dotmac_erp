"""
Automation Background Tasks — Celery tasks for workflow rule execution.

Handles:
- Async workflow action execution (dispatched from WorkflowService)
- Scheduled workflow rule evaluation
"""

import logging
from typing import Any

from celery import shared_task

from app.db import SessionLocal

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def execute_workflow_action(
    self: Any,
    rule_id: str,
    context_dict: dict[str, Any],
) -> dict[str, Any]:
    """Execute a single workflow action asynchronously.

    Called by WorkflowService.trigger_event() when a rule has
    execute_async=True.

    Args:
        rule_id: UUID string of the workflow rule to execute.
        context_dict: Serialized TriggerContext (from TriggerContext.to_dict()).

    Returns:
        Dict with execution result.
    """
    from uuid import UUID

    logger.info("Executing async workflow action: rule=%s", rule_id)

    result: dict[str, Any] = {
        "rule_id": rule_id,
        "status": "unknown",
        "error": None,
    }

    with SessionLocal() as db:
        try:
            from app.services.finance.automation.workflow import (
                TriggerContext,
                workflow_service,
            )

            rule = workflow_service.get(db, UUID(rule_id))
            if not rule:
                result["status"] = "rule_not_found"
                result["error"] = f"Rule {rule_id} not found"
                logger.warning("Workflow rule %s not found", rule_id)
                return result

            context = TriggerContext.from_dict(context_dict)

            # Check throttle before executing
            if workflow_service._is_throttled(db, rule, context.entity_id):
                result["status"] = "throttled"
                logger.info(
                    "Rule %s throttled for entity %s",
                    rule_id,
                    context.entity_id,
                )
                return result

            execution = workflow_service.execute_action(db, rule, context)
            db.commit()

            result["status"] = execution.status.value
            result["execution_id"] = str(execution.execution_id)

        except Exception as exc:
            logger.exception("Async workflow action failed: rule=%s", rule_id)
            result["status"] = "error"
            result["error"] = str(exc)

            # Retry on transient failures
            try:
                self.retry(exc=exc)
            except self.MaxRetriesExceededError:
                logger.error("Max retries exceeded for rule %s", rule_id)

    return result


@shared_task
def process_scheduled_workflow_rules() -> dict[str, Any]:
    """Evaluate and execute all due ON_SCHEDULE workflow rules.

    This task should be run on a periodic schedule (e.g. every 5 minutes)
    via Celery beat.

    Returns:
        Dict with processing statistics.
    """
    logger.info("Processing scheduled workflow rules")

    with SessionLocal() as db:
        from app.services.finance.automation.scheduled_evaluator import (
            scheduled_evaluator,
        )

        results = scheduled_evaluator.evaluate_due_rules(db)
        db.commit()

    logger.info(
        "Scheduled rules: checked=%d, due=%d, fired=%d, errors=%d",
        results["rules_checked"],
        results["rules_due"],
        results["actions_fired"],
        len(results["errors"]),
    )
    return results
