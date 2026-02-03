"""
Tests for Workflow Engine.

Tests all action handlers, condition evaluation (flat and compound),
throttle/cooldown logic, template rendering, entity registry, and
rate limiter caps.
"""

import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.models.finance.automation import ExecutionStatus
from app.models.finance.automation.workflow_rule import (
    ActionType,
    TriggerEvent,
    WorkflowEntityType,
)
from app.services.finance.automation.workflow import (
    TriggerContext,
    WorkflowService,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workflow_service() -> WorkflowService:
    return WorkflowService()


@pytest.fixture
def sample_context() -> TriggerContext:
    return TriggerContext(
        entity_type="EXPENSE",
        entity_id=uuid.uuid4(),
        event=TriggerEvent.ON_APPROVAL,
        organization_id=uuid.uuid4(),
        old_values={"status": "SUBMITTED", "total_amount": "500"},
        new_values={"status": "APPROVED", "total_amount": "500"},
        user_id=uuid.uuid4(),
    )


def _make_mock_rule(**overrides: Any) -> MagicMock:
    """Create a mock WorkflowRule with sensible defaults."""
    rule = MagicMock()
    rule.rule_id = overrides.get("rule_id", uuid.uuid4())
    rule.organization_id = overrides.get("organization_id", uuid.uuid4())
    rule.entity_type = overrides.get("entity_type", WorkflowEntityType.EXPENSE)
    rule.trigger_event = overrides.get("trigger_event", TriggerEvent.ON_APPROVAL)
    rule.trigger_conditions = overrides.get("trigger_conditions", {})
    rule.action_type = overrides.get("action_type", ActionType.SEND_NOTIFICATION)
    rule.action_config = overrides.get("action_config", {})
    rule.priority = overrides.get("priority", 100)
    rule.stop_on_match = overrides.get("stop_on_match", False)
    rule.execute_async = overrides.get("execute_async", False)
    rule.cooldown_seconds = overrides.get("cooldown_seconds", None)
    rule.is_active = overrides.get("is_active", True)
    rule.execution_count = 0
    rule.success_count = 0
    rule.failure_count = 0
    rule.last_executed_at = None
    rule.schedule_config = overrides.get("schedule_config", None)
    return rule


# ---------------------------------------------------------------------------
# TriggerContext serialization
# ---------------------------------------------------------------------------


class TestTriggerContextSerialization:
    def test_to_dict_and_from_dict_roundtrip(self, sample_context: TriggerContext):
        data = sample_context.to_dict()

        assert isinstance(data, dict)
        assert data["entity_type"] == "EXPENSE"
        assert data["event"] == "ON_APPROVAL"

        restored = TriggerContext.from_dict(data)

        assert restored.entity_type == sample_context.entity_type
        assert restored.entity_id == sample_context.entity_id
        assert restored.event == sample_context.event
        assert restored.organization_id == sample_context.organization_id
        assert restored.old_values == sample_context.old_values
        assert restored.new_values == sample_context.new_values
        assert restored.user_id == sample_context.user_id

    def test_from_dict_with_none_optionals(self):
        data = {
            "entity_type": "INVOICE",
            "entity_id": str(uuid.uuid4()),
            "event": "ON_STATUS_CHANGE",
        }
        ctx = TriggerContext.from_dict(data)
        assert ctx.organization_id is None
        assert ctx.user_id is None
        assert ctx.old_values is None


# ---------------------------------------------------------------------------
# Flat Condition Evaluation
# ---------------------------------------------------------------------------


class TestFlatConditionEvaluation:
    def test_empty_conditions_match(self, workflow_service, sample_context):
        assert workflow_service._evaluate_conditions({}, sample_context) is True

    def test_field_equality(self, workflow_service, sample_context):
        conditions = {"fields": {"status": "APPROVED"}}
        assert workflow_service._evaluate_conditions(conditions, sample_context) is True

        conditions = {"fields": {"status": "REJECTED"}}
        assert (
            workflow_service._evaluate_conditions(conditions, sample_context) is False
        )

    def test_field_operator_comparison(self, workflow_service, sample_context):
        conditions = {
            "fields": {"total_amount": {"operator": "greater_than", "value": "100"}}
        }
        assert workflow_service._evaluate_conditions(conditions, sample_context) is True

    def test_status_transition_check(self, workflow_service, sample_context):
        conditions = {"status_from": "SUBMITTED", "status_to": "APPROVED"}
        assert workflow_service._evaluate_conditions(conditions, sample_context) is True

        conditions = {"status_from": "DRAFT", "status_to": "APPROVED"}
        assert (
            workflow_service._evaluate_conditions(conditions, sample_context) is False
        )

    def test_amount_threshold(self, workflow_service, sample_context):
        conditions = {
            "amount_threshold": {
                "field": "total_amount",
                "operator": "greater_than",
                "value": 100,
            }
        }
        assert workflow_service._evaluate_conditions(conditions, sample_context) is True

        conditions = {
            "amount_threshold": {
                "field": "total_amount",
                "operator": "greater_than",
                "value": 1000,
            }
        }
        assert (
            workflow_service._evaluate_conditions(conditions, sample_context) is False
        )


# ---------------------------------------------------------------------------
# Compound Condition Evaluation
# ---------------------------------------------------------------------------


class TestCompoundConditionEvaluation:
    def test_and_group(self, workflow_service, sample_context):
        conditions = {
            "operator": "AND",
            "groups": [
                {"field": "status", "operator": "equals", "value": "APPROVED"},
                {"field": "total_amount", "operator": "equals", "value": "500"},
            ],
        }
        assert workflow_service._evaluate_conditions(conditions, sample_context) is True

    def test_or_group(self, workflow_service, sample_context):
        conditions = {
            "operator": "OR",
            "groups": [
                {"field": "status", "operator": "equals", "value": "REJECTED"},
                {"field": "total_amount", "operator": "equals", "value": "500"},
            ],
        }
        assert workflow_service._evaluate_conditions(conditions, sample_context) is True

    def test_and_group_fails_when_one_fails(self, workflow_service, sample_context):
        conditions = {
            "operator": "AND",
            "groups": [
                {"field": "status", "operator": "equals", "value": "APPROVED"},
                {"field": "total_amount", "operator": "equals", "value": "9999"},
            ],
        }
        assert (
            workflow_service._evaluate_conditions(conditions, sample_context) is False
        )

    def test_nested_and_or(self, workflow_service, sample_context):
        conditions = {
            "operator": "AND",
            "groups": [
                {
                    "operator": "OR",
                    "conditions": [
                        {"field": "status", "operator": "equals", "value": "APPROVED"},
                        {"field": "status", "operator": "equals", "value": "REJECTED"},
                    ],
                },
                {"field": "total_amount", "operator": "equals", "value": "500"},
            ],
        }
        assert workflow_service._evaluate_conditions(conditions, sample_context) is True

    def test_backward_compat_flat_conditions(self, workflow_service, sample_context):
        """Flat conditions (no operator/groups) should still work."""
        conditions = {"fields": {"status": "APPROVED"}}
        assert workflow_service._evaluate_conditions(conditions, sample_context) is True


# ---------------------------------------------------------------------------
# Value Comparison
# ---------------------------------------------------------------------------


class TestValueComparison:
    def test_operators(self, workflow_service):
        assert workflow_service._compare_values(10, "equals", 10) is True
        assert workflow_service._compare_values(10, "not_equals", 5) is True
        assert workflow_service._compare_values(10, "greater_than", 5) is True
        assert workflow_service._compare_values(10, "less_than", 20) is True
        assert workflow_service._compare_values("hello", "contains", "ell") is True
        assert workflow_service._compare_values("hello", "starts_with", "hel") is True
        assert workflow_service._compare_values("hello", "ends_with", "llo") is True
        assert workflow_service._compare_values("abc", "matches", r"a.c") is True
        assert workflow_service._compare_values(3, "in", [1, 2, 3]) is True
        assert workflow_service._compare_values(4, "not_in", [1, 2, 3]) is True
        assert workflow_service._compare_values(None, "is_null", None) is True
        assert workflow_service._compare_values(5, "is_not_null", None) is True

    def test_none_value(self, workflow_service):
        assert workflow_service._compare_values(None, "equals", None) is True
        assert workflow_service._compare_values(None, "equals", 5) is False


# ---------------------------------------------------------------------------
# Template Renderer
# ---------------------------------------------------------------------------


class TestTemplateRenderer:
    def test_basic_rendering(self):
        from app.services.finance.automation.template_renderer import render_template

        result = render_template(
            "Invoice {{ entity_id }} {{ new.status }}",
            entity_type="INVOICE",
            entity_id=uuid.UUID("12345678-1234-1234-1234-123456789012"),
            new_values={"status": "APPROVED"},
        )
        assert "12345678-1234-1234-1234-123456789012" in result
        assert "APPROVED" in result

    def test_empty_template(self):
        from app.services.finance.automation.template_renderer import render_template

        assert render_template("", entity_type="X", entity_id=None) == ""

    def test_invalid_syntax_falls_back(self):
        from app.services.finance.automation.template_renderer import render_template

        result = render_template(
            "Hello {{ broken syntax",
            entity_type="INVOICE",
            entity_id=uuid.uuid4(),
        )
        # Should return something (fallback) rather than crash
        assert isinstance(result, str)

    def test_old_new_values(self):
        from app.services.finance.automation.template_renderer import render_template

        result = render_template(
            "Changed from {{ old.status }} to {{ new.status }}",
            entity_type="EXPENSE",
            entity_id=uuid.uuid4(),
            old_values={"status": "DRAFT"},
            new_values={"status": "SUBMITTED"},
        )
        assert "DRAFT" in result
        assert "SUBMITTED" in result


# ---------------------------------------------------------------------------
# Entity Registry
# ---------------------------------------------------------------------------


class TestEntityRegistry:
    def test_get_registered_types(self):
        from app.services.finance.automation.entity_registry import (
            get_registered_types,
        )

        types = get_registered_types()
        assert "INVOICE" in types
        assert "EXPENSE" in types
        assert "LEAVE_REQUEST" in types
        assert "PAYROLL_RUN" in types
        assert "FLEET_MAINTENANCE" in types
        assert "ASSET_DISPOSAL" in types

    def test_get_pk_field(self):
        from app.services.finance.automation.entity_registry import get_pk_field

        assert get_pk_field("INVOICE") == "invoice_id"
        assert get_pk_field("EXPENSE") == "claim_id"
        assert get_pk_field("PAYROLL_RUN") == "entry_id"
        assert get_pk_field("UNKNOWN_TYPE") is None


# ---------------------------------------------------------------------------
# Action Handlers (with mocked DB)
# ---------------------------------------------------------------------------


class TestActionValidate:
    def test_validate_passes(self, workflow_service, sample_context):
        config = {
            "rules": [
                {
                    "field": "status",
                    "condition": {"operator": "equals", "value": "APPROVED"},
                    "message": "Status must be APPROVED",
                }
            ]
        }
        result = workflow_service._action_validate(MagicMock(), config, sample_context)
        assert result.success is True

    def test_validate_fails(self, workflow_service, sample_context):
        config = {
            "rules": [
                {
                    "field": "status",
                    "condition": {"operator": "equals", "value": "REJECTED"},
                    "message": "Status must be REJECTED",
                }
            ]
        }
        result = workflow_service._action_validate(MagicMock(), config, sample_context)
        assert result.success is False
        assert "Status must be REJECTED" in result.error_message


class TestActionBlock:
    def test_block_returns_failure(self, workflow_service, sample_context):
        config = {"message": "Blocked by policy"}
        result = workflow_service._action_block(MagicMock(), config, sample_context)
        assert result.success is False
        assert result.result["blocked"] is True
        assert "Blocked by policy" in result.error_message


class TestActionSendNotification:
    def test_no_recipients_fails(self, workflow_service, sample_context):
        config = {"recipient_ids": []}
        result = workflow_service._action_send_notification(
            MagicMock(), config, sample_context
        )
        assert result.success is False

    @patch("app.services.notification.NotificationService.create")
    def test_sends_to_recipients(self, mock_create, workflow_service, sample_context):
        mock_create.return_value = MagicMock()
        recipient_id = str(uuid.uuid4())
        config = {
            "recipient_ids": [recipient_id],
            "title": "Test {{ new.status }}",
            "message": "Entity {{ entity_id }}",
        }
        result = workflow_service._action_send_notification(
            MagicMock(), config, sample_context
        )
        assert result.success is True
        assert recipient_id in result.result["sent_to"]


class TestActionCreateTask:
    def test_no_project_id_fails(self, workflow_service, sample_context):
        config = {"title": "Test task"}
        result = workflow_service._action_create_task(
            MagicMock(), config, sample_context
        )
        assert result.success is False
        assert "project_id" in result.error_message


class TestActionUpdateField:
    def test_no_field_fails(self, workflow_service, sample_context):
        config = {"value": "test"}
        result = workflow_service._action_update_field(
            MagicMock(), config, sample_context
        )
        assert result.success is False
        assert "field" in result.error_message


# ---------------------------------------------------------------------------
# Dry Run
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_matching(self, workflow_service):
        mock_db = MagicMock()
        rule = _make_mock_rule(
            trigger_conditions={"status_to": "APPROVED"},
        )
        mock_db.get.return_value = rule
        mock_db.scalar.return_value = 0  # not throttled

        result = workflow_service.dry_run(
            mock_db,
            rule.rule_id,
            {
                "entity_id": str(uuid.uuid4()),
                "old_values": {"status": "SUBMITTED"},
                "new_values": {"status": "APPROVED"},
            },
        )
        assert result["conditions_match"] is True
        assert result["would_fire"] is True
        assert result["throttled"] is False

    def test_dry_run_not_matching(self, workflow_service):
        mock_db = MagicMock()
        rule = _make_mock_rule(
            trigger_conditions={"status_to": "REJECTED"},
        )
        mock_db.get.return_value = rule

        result = workflow_service.dry_run(
            mock_db,
            rule.rule_id,
            {
                "entity_id": str(uuid.uuid4()),
                "new_values": {"status": "APPROVED"},
            },
        )
        assert result["conditions_match"] is False
        assert result["would_fire"] is False


# ---------------------------------------------------------------------------
# Event Dispatcher
# ---------------------------------------------------------------------------


class TestEventDispatcher:
    @patch("app.services.finance.automation.workflow.WorkflowService.trigger_event")
    def test_fire_workflow_event(self, mock_trigger):
        from app.services.finance.automation.event_dispatcher import (
            fire_workflow_event,
        )

        mock_db = MagicMock()
        org_id = uuid.uuid4()
        entity_id = uuid.uuid4()

        fire_workflow_event(
            db=mock_db,
            organization_id=org_id,
            entity_type="EXPENSE",
            entity_id=entity_id,
            event="ON_APPROVAL",
            old_values={"status": "SUBMITTED"},
            new_values={"status": "APPROVED"},
        )

        mock_trigger.assert_called_once()
        call_args = mock_trigger.call_args
        assert call_args[0][1] == org_id

    def test_fire_unknown_event_is_noop(self):
        from app.services.finance.automation.event_dispatcher import (
            fire_workflow_event,
        )

        # Should not raise
        fire_workflow_event(
            db=MagicMock(),
            organization_id=uuid.uuid4(),
            entity_type="EXPENSE",
            entity_id=uuid.uuid4(),
            event="UNKNOWN_EVENT_XYZ",
        )


# ---------------------------------------------------------------------------
# Rule Chaining
# ---------------------------------------------------------------------------


class TestActionTriggerRule:
    def test_trigger_rule_respects_conditions(self, workflow_service, sample_context):
        target_rule = _make_mock_rule(
            rule_id=uuid.uuid4(),
            trigger_event=TriggerEvent.ON_APPROVAL,
            trigger_conditions={"status_to": "APPROVED"},
            action_type=ActionType.BLOCK,
        )
        mock_db = MagicMock()
        mock_db.get.return_value = target_rule

        with patch.object(workflow_service, "_check_entity_rate_limit", return_value=False), \
            patch.object(workflow_service, "_is_throttled", return_value=False), \
            patch.object(workflow_service, "execute_action") as mock_execute:
            mock_execute.return_value = MagicMock(
                status=ExecutionStatus.SUCCESS,
                execution_id=uuid.uuid4(),
                error_message=None,
            )

            result = workflow_service._action_trigger_rule(
                mock_db,
                {"rule_id": str(target_rule.rule_id)},
                sample_context,
            )

        assert result.success is True
        mock_execute.assert_called_once()

    def test_trigger_rule_mismatched_event(self, workflow_service, sample_context):
        target_rule = _make_mock_rule(
            rule_id=uuid.uuid4(),
            trigger_event=TriggerEvent.ON_REJECTION,
        )
        mock_db = MagicMock()
        mock_db.get.return_value = target_rule

        result = workflow_service._action_trigger_rule(
            mock_db,
            {"rule_id": str(target_rule.rule_id)},
            sample_context,
        )

        assert result.success is False
        assert "trigger_event" in (result.error_message or "")


# ---------------------------------------------------------------------------
# Scheduled Rules
# ---------------------------------------------------------------------------


class TestScheduledRules:
    def test_evaluate_due_rules_executes(self):
        from app.services.finance.automation.scheduled_evaluator import (
            ScheduledRuleEvaluator,
        )

        evaluator = ScheduledRuleEvaluator()
        mock_db = MagicMock()
        rule = _make_mock_rule(
            trigger_event=TriggerEvent.ON_SCHEDULE,
            schedule_config={"interval_minutes": 5},
        )
        rule.entity_type = WorkflowEntityType.EXPENSE
        rule.organization_id = uuid.uuid4()
        rule.last_executed_at = None

        mock_db.scalars.return_value.all.return_value = [rule]

        with patch.object(evaluator, "_is_due", return_value=True), \
            patch.object(evaluator, "_find_matching_entities", return_value=[uuid.uuid4(), uuid.uuid4()]), \
            patch("app.services.finance.automation.workflow.workflow_service._is_throttled", return_value=False), \
            patch("app.services.finance.automation.workflow.workflow_service.execute_action") as mock_execute:
            result = evaluator.evaluate_due_rules(mock_db)

        assert result["rules_checked"] == 1
        assert result["rules_due"] == 1
        assert result["actions_fired"] == 2
        assert mock_execute.call_count == 2


# ---------------------------------------------------------------------------
# Rule Versioning
# ---------------------------------------------------------------------------


class TestRuleVersioning:
    def test_update_rule_creates_snapshot(self, workflow_service):
        mock_db = MagicMock()
        rule = _make_mock_rule()
        mock_db.get.return_value = rule

        with patch.object(workflow_service, "_create_version_snapshot") as snapshot:
            workflow_service.update_rule(
                mock_db,
                rule.rule_id,
                {"priority": 10},
                updated_by=uuid.uuid4(),
            )

        snapshot.assert_called_once()


# ---------------------------------------------------------------------------
# Rate Limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    def test_max_rules_per_event_constant(self, workflow_service):
        assert workflow_service.MAX_RULES_PER_EVENT == 10

    def test_max_executions_per_minute_constant(self, workflow_service):
        assert workflow_service.MAX_EXECUTIONS_PER_MINUTE == 50

    def test_check_entity_rate_limit_under_limit(self, workflow_service):
        mock_db = MagicMock()
        mock_db.scalar.return_value = 5  # Under the 50 limit
        assert workflow_service._check_entity_rate_limit(mock_db, uuid.uuid4()) is False

    def test_check_entity_rate_limit_over_limit(self, workflow_service):
        mock_db = MagicMock()
        mock_db.scalar.return_value = 51  # Over the 50 limit
        assert workflow_service._check_entity_rate_limit(mock_db, uuid.uuid4()) is True
