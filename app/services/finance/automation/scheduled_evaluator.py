"""
Scheduled Rule Evaluator.

Handles ON_SCHEDULE workflow rules that fire on a periodic basis
rather than in response to entity events. A Celery beat task calls
``evaluate_due_rules()`` every few minutes, which:

1. Queries all active ON_SCHEDULE rules.
2. Checks if each rule is "due" based on ``last_executed_at`` plus
   the configured interval.
3. For each due rule, queries matching entities using the registry
   and fires the action per entity.
"""

import logging
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.automation import (
    TriggerEvent,
    WorkflowRule,
)

logger = logging.getLogger(__name__)


class ScheduledRuleEvaluator:
    """Evaluate and execute ON_SCHEDULE workflow rules."""

    def evaluate_due_rules(self, db: Session) -> dict[str, Any]:
        """Find and execute all due ON_SCHEDULE rules.

        Returns:
            Dict with counts of rules evaluated, actions fired, and errors.
        """
        from app.services.finance.automation.workflow import (
            TriggerContext,
            workflow_service,
        )

        results: dict[str, Any] = {
            "rules_checked": 0,
            "rules_due": 0,
            "actions_fired": 0,
            "errors": [],
        }

        # Find all active ON_SCHEDULE rules
        stmt = select(WorkflowRule).where(
            WorkflowRule.is_active.is_(True),
            WorkflowRule.trigger_event == TriggerEvent.ON_SCHEDULE,
        )
        rules = list(db.scalars(stmt).all())
        results["rules_checked"] = len(rules)

        for rule in rules:
            try:
                if not self._is_due(rule):
                    continue

                results["rules_due"] += 1

                # Get entities matching the rule's conditions
                entity_ids = self._find_matching_entities(db, rule)

                for entity_id in entity_ids:
                    # Check throttle
                    if workflow_service._is_throttled(db, rule, entity_id):
                        continue

                    context = TriggerContext(
                        entity_type=rule.entity_type.value,
                        entity_id=entity_id,
                        event=TriggerEvent.ON_SCHEDULE,
                        organization_id=rule.organization_id,
                    )

                    try:
                        workflow_service.execute_action(db, rule, context)
                        results["actions_fired"] += 1
                    except Exception as e:
                        logger.exception(
                            "Error executing scheduled rule %s for entity %s",
                            rule.rule_id,
                            entity_id,
                        )
                        results["errors"].append(str(e))

                # Update last_executed_at to prevent re-firing
                rule.last_executed_at = datetime.utcnow()
                db.flush()

            except Exception as e:
                logger.exception("Error evaluating scheduled rule %s", rule.rule_id)
                results["errors"].append(str(e))

        return results

    def _is_due(self, rule: WorkflowRule) -> bool:
        """Check if a scheduled rule is due for execution."""
        config = rule.schedule_config or {}
        interval_minutes = config.get("interval_minutes", 60)

        if not rule.last_executed_at:
            return True

        next_run = rule.last_executed_at + timedelta(minutes=interval_minutes)
        return datetime.utcnow() >= next_run

    def _find_matching_entities(
        self,
        db: Session,
        rule: WorkflowRule,
    ) -> list[UUID]:
        """Find entities matching the rule's conditions for scheduled execution.

        Uses the entity registry to build queries against the target entity type.
        """
        from app.services.finance.automation.entity_registry import (
            _get_model_class,
            get_pk_field,
        )

        model_cls = _get_model_class(rule.entity_type.value)
        if model_cls is None:
            logger.warning(
                "Cannot resolve model for entity type %s",
                rule.entity_type.value,
            )
            return []

        pk_field = get_pk_field(rule.entity_type.value)
        if not pk_field:
            return []

        # Build query for this entity type + organization
        pk_col = getattr(model_cls, pk_field, None)
        if pk_col is None:
            return []

        stmt = select(pk_col)

        # Filter by organization if the model has the field
        if hasattr(model_cls, "organization_id"):
            stmt = stmt.where(model_cls.organization_id == rule.organization_id)

        # Apply entity_query filters from schedule_config
        config = rule.schedule_config or {}
        entity_query = config.get("entity_query", {})

        # Simple status filter
        status_filter = entity_query.get("status")
        if status_filter and hasattr(model_cls, "status"):
            stmt = stmt.where(model_cls.status == status_filter)

        # Apply additional field filters
        filters = entity_query.get("filters", [])
        for f in filters:
            field_name = f.get("field")
            if not field_name or not hasattr(model_cls, field_name):
                continue
            col = getattr(model_cls, field_name)
            op = f.get("operator", "equals")
            val = f.get("value")

            if op == "equals":
                stmt = stmt.where(col == val)
            elif op == "not_equals":
                stmt = stmt.where(col != val)
            elif op == "greater_than":
                stmt = stmt.where(col > val)
            elif op == "less_than":
                stmt = stmt.where(col < val)
            elif op == "is_null":
                stmt = stmt.where(col.is_(None))
            elif op == "is_not_null":
                stmt = stmt.where(col.isnot(None))

        # Limit results to prevent runaway queries
        stmt = stmt.limit(200)

        return list(db.scalars(stmt).all())


# Singleton
scheduled_evaluator = ScheduledRuleEvaluator()
