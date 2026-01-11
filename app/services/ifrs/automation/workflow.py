"""
Workflow Service.

Handles workflow rule evaluation and action execution.
"""
import ipaddress
import logging
import os
import re
import socket
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.ifrs.automation import (
    ActionType,
    ExecutionStatus,
    TriggerEvent,
    WorkflowEntityType,
    WorkflowExecution,
    WorkflowRule,
)

logger = logging.getLogger(__name__)

_HEADER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")
_ALLOWED_WEBHOOK_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def _db_setting(db: Session | None, key: str) -> object | None:
    if db is None:
        return None
    try:
        from app.services.domain_settings import automation_settings
        setting = automation_settings.get_by_key(db, key)
    except HTTPException:
        return None
    if setting.value_text is not None:
        return setting.value_text
    if setting.value_json is not None:
        return setting.value_json
    return None


def _coerce_csv(value: object | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = [str(item) for item in value]
    else:
        items = str(value).split(",")
    return [item.strip() for item in items if item and str(item).strip()]


def _coerce_bool(value: object | None, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _coerce_float(value: object | None, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return default


def _allowed_webhook_hosts(db: Session | None = None) -> set[str]:
    raw = _db_setting(db, "webhook_allowed_hosts")
    if raw is None:
        raw = os.getenv("WEBHOOK_ALLOWED_HOSTS", "")
    return {item.strip().lower() for item in _coerce_csv(raw)}


def _allowed_webhook_domains(db: Session | None = None) -> set[str]:
    raw = _db_setting(db, "webhook_allowed_domains")
    if raw is None:
        raw = os.getenv("WEBHOOK_ALLOWED_DOMAINS", "")
    return {item.strip().lower().lstrip(".") for item in _coerce_csv(raw)}


def _allow_insecure_webhooks(db: Session | None = None) -> bool:
    raw = _db_setting(db, "webhook_allow_insecure")
    if raw is None:
        raw = os.getenv("WEBHOOK_ALLOW_INSECURE")
    return _coerce_bool(raw, default=False)


def _allow_localhost_webhooks(db: Session | None = None) -> bool:
    raw = _db_setting(db, "webhook_allow_localhost")
    if raw is None:
        raw = os.getenv("WEBHOOK_ALLOW_LOCALHOST")
    return _coerce_bool(raw, default=False)


def _webhook_timeout(db: Session | None = None) -> float:
    raw = _db_setting(db, "webhook_timeout_seconds")
    if raw is None:
        raw = os.getenv("WEBHOOK_TIMEOUT_SECONDS")
    return _coerce_float(raw, default=10.0)


def _is_private_address(address: ipaddress._BaseAddress) -> bool:
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _host_matches_allowlist(host: str, db: Session | None = None) -> bool:
    host = host.lower()
    allowed_hosts = _allowed_webhook_hosts(db)
    allowed_domains = _allowed_webhook_domains(db)
    if allowed_hosts and host in allowed_hosts:
        return True
    if allowed_domains:
        for domain in allowed_domains:
            if host == domain or host.endswith(f".{domain}"):
                return True
    return not allowed_hosts and not allowed_domains


def _validate_webhook_target(url: str, db: Session | None = None) -> tuple[bool, str | None]:
    if not url or not isinstance(url, str):
        return False, "Webhook URL is required"

    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"}:
        return False, "Webhook URL must use http or https"
    if parsed.username or parsed.password:
        return False, "Webhook URL must not include credentials"
    if not parsed.hostname:
        return False, "Webhook URL must include a host"

    host = parsed.hostname
    if not _host_matches_allowlist(host, db):
        return False, "Webhook host is not in the allowlist"

    allow_localhost = _allow_localhost_webhooks(db)
    require_https = parsed.scheme == "http" and not _allow_insecure_webhooks(db)
    loopback_host = False

    try:
        ip_value = ipaddress.ip_address(host)
        if ip_value.is_loopback:
            loopback_host = True
        if _is_private_address(ip_value):
            if not (allow_localhost and ip_value.is_loopback):
                return False, "Webhook target is not allowed"
    except ValueError:
        try:
            addr_info = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return False, "Webhook host could not be resolved"
        for _, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            ip_value = ipaddress.ip_address(ip_str)
            if ip_value.is_loopback:
                loopback_host = True
            if _is_private_address(ip_value):
                if not (allow_localhost and ip_value.is_loopback):
                    return False, "Webhook target is not allowed"

    if require_https and not (allow_localhost and loopback_host):
        return False, "Webhook URL must use https"

    return True, None


@dataclass
class WorkflowRuleInput:
    """Input for creating a workflow rule."""
    rule_name: str
    entity_type: WorkflowEntityType
    trigger_event: TriggerEvent
    action_type: ActionType
    trigger_conditions: Dict[str, Any]
    action_config: Dict[str, Any]
    description: Optional[str] = None
    priority: int = 100
    stop_on_match: bool = False
    execute_async: bool = True


@dataclass
class TriggerContext:
    """Context for evaluating workflow triggers."""
    entity_type: str
    entity_id: UUID
    event: TriggerEvent
    old_values: Optional[Dict[str, Any]] = None
    new_values: Optional[Dict[str, Any]] = None
    changed_fields: Optional[List[str]] = None
    user_id: Optional[UUID] = None


@dataclass
class ActionResult:
    """Result of executing a workflow action."""
    success: bool
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None


class WorkflowService:
    """Service for managing and executing workflow rules."""

    def create_rule(
        self,
        db: Session,
        organization_id: UUID,
        input_data: WorkflowRuleInput,
        created_by: UUID,
    ) -> WorkflowRule:
        """Create a new workflow rule."""
        # Check for duplicate name
        existing = db.execute(
            select(WorkflowRule).where(
                and_(
                    WorkflowRule.organization_id == organization_id,
                    WorkflowRule.rule_name == input_data.rule_name,
                )
            )
        ).scalar_one_or_none()

        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Rule with name '{input_data.rule_name}' already exists",
            )

        rule = WorkflowRule(
            organization_id=organization_id,
            rule_name=input_data.rule_name,
            description=input_data.description,
            entity_type=input_data.entity_type,
            trigger_event=input_data.trigger_event,
            trigger_conditions=input_data.trigger_conditions,
            action_type=input_data.action_type,
            action_config=input_data.action_config,
            priority=input_data.priority,
            stop_on_match=input_data.stop_on_match,
            execute_async=input_data.execute_async,
            created_by=created_by,
        )

        db.add(rule)
        db.flush()
        return rule

    def get(self, db: Session, rule_id: UUID) -> Optional[WorkflowRule]:
        """Get a rule by ID."""
        return db.get(WorkflowRule, rule_id)

    def list(
        self,
        db: Session,
        organization_id: UUID,
        entity_type: Optional[WorkflowEntityType] = None,
        trigger_event: Optional[TriggerEvent] = None,
        is_active: Optional[bool] = True,
        limit: int = 100,
        offset: int = 0,
    ) -> List[WorkflowRule]:
        """List workflow rules."""
        query = select(WorkflowRule).where(
            WorkflowRule.organization_id == organization_id
        )

        if entity_type:
            query = query.where(WorkflowRule.entity_type == entity_type)
        if trigger_event:
            query = query.where(WorkflowRule.trigger_event == trigger_event)
        if is_active is not None:
            query = query.where(WorkflowRule.is_active == is_active)

        query = query.order_by(WorkflowRule.priority, WorkflowRule.created_at.desc())
        query = query.offset(offset).limit(limit)

        return list(db.execute(query).scalars().all())

    def get_matching_rules(
        self,
        db: Session,
        organization_id: UUID,
        context: TriggerContext,
    ) -> List[WorkflowRule]:
        """Get all rules that match a trigger context."""
        # Convert string entity type to enum
        try:
            entity_type = WorkflowEntityType(context.entity_type)
        except ValueError:
            return []

        rules = self.list(
            db,
            organization_id,
            entity_type=entity_type,
            trigger_event=context.event,
            is_active=True,
        )

        matching = []
        for rule in rules:
            if self._evaluate_conditions(rule.trigger_conditions, context):
                matching.append(rule)

        return matching

    def _evaluate_conditions(
        self,
        conditions: Dict[str, Any],
        context: TriggerContext,
    ) -> bool:
        """Evaluate if conditions are met for a trigger context."""
        if not conditions:
            return True

        values = context.new_values or {}

        # Field comparisons
        field_conditions = conditions.get("fields", {})
        for field, condition in field_conditions.items():
            field_value = values.get(field)

            if isinstance(condition, dict):
                operator = condition.get("operator", "equals")
                expected = condition.get("value")

                if not self._compare_values(field_value, operator, expected):
                    return False
            else:
                # Simple equality check
                if field_value != condition:
                    return False

        # Status transition check
        status_from = conditions.get("status_from")
        status_to = conditions.get("status_to")
        if status_from or status_to:
            old_status = context.old_values.get("status") if context.old_values else None
            new_status = values.get("status")

            if status_from and old_status != status_from:
                return False
            if status_to and new_status != status_to:
                return False

        # Changed fields check
        required_changes = conditions.get("changed_fields", [])
        if required_changes and context.changed_fields:
            if not any(f in context.changed_fields for f in required_changes):
                return False

        # Amount threshold check
        amount_threshold = conditions.get("amount_threshold")
        if amount_threshold:
            amount_field = amount_threshold.get("field", "total_amount")
            threshold_value = Decimal(str(amount_threshold.get("value", 0)))
            operator = amount_threshold.get("operator", "greater_than")

            amount_value = values.get(amount_field)
            if amount_value is not None:
                amount_value = Decimal(str(amount_value))
                if not self._compare_values(amount_value, operator, threshold_value):
                    return False

        return True

    def _compare_values(self, value: Any, operator: str, expected: Any) -> bool:
        """Compare values using the specified operator."""
        if value is None:
            return operator == "is_null" or (operator == "equals" and expected is None)

        if operator == "equals":
            return value == expected
        elif operator == "not_equals":
            return value != expected
        elif operator == "greater_than":
            return value > expected
        elif operator == "greater_than_or_equal":
            return value >= expected
        elif operator == "less_than":
            return value < expected
        elif operator == "less_than_or_equal":
            return value <= expected
        elif operator == "contains":
            return str(expected).lower() in str(value).lower()
        elif operator == "starts_with":
            return str(value).lower().startswith(str(expected).lower())
        elif operator == "ends_with":
            return str(value).lower().endswith(str(expected).lower())
        elif operator == "matches":
            return bool(re.match(str(expected), str(value), re.IGNORECASE))
        elif operator == "in":
            return value in expected
        elif operator == "not_in":
            return value not in expected
        elif operator == "is_null":
            return value is None
        elif operator == "is_not_null":
            return value is not None

        return False

    def execute_action(
        self,
        db: Session,
        rule: WorkflowRule,
        context: TriggerContext,
    ) -> WorkflowExecution:
        """Execute a workflow rule action."""
        execution = WorkflowExecution(
            rule_id=rule.rule_id,
            entity_type=context.entity_type,
            entity_id=context.entity_id,
            trigger_event=context.event.value,
            trigger_data={
                "old_values": context.old_values,
                "new_values": context.new_values,
                "changed_fields": context.changed_fields,
            },
            triggered_by=context.user_id,
            status=ExecutionStatus.RUNNING,
            started_at=datetime.utcnow(),
        )
        db.add(execution)
        db.flush()

        try:
            result = self._run_action(db, rule, context)

            execution.status = ExecutionStatus.SUCCESS if result.success else ExecutionStatus.FAILED
            execution.result = result.result
            execution.error_message = result.error_message
            execution.completed_at = datetime.utcnow()

            if execution.started_at:
                duration = (execution.completed_at - execution.started_at).total_seconds() * 1000
                execution.duration_ms = int(duration)

            # Update rule statistics
            rule.execution_count += 1
            if result.success:
                rule.success_count += 1
            else:
                rule.failure_count += 1
            rule.last_executed_at = datetime.utcnow()

        except Exception as e:
            logger.exception("Error executing workflow action for rule %s", rule.rule_id)
            execution.status = ExecutionStatus.FAILED
            execution.error_message = str(e)
            execution.completed_at = datetime.utcnow()
            rule.execution_count += 1
            rule.failure_count += 1
            rule.last_executed_at = datetime.utcnow()

        db.flush()
        return execution

    def _run_action(
        self,
        db: Session,
        rule: WorkflowRule,
        context: TriggerContext,
    ) -> ActionResult:
        """Run the action for a workflow rule."""
        config = rule.action_config

        if rule.action_type == ActionType.SEND_EMAIL:
            return self._action_send_email(db, config, context)
        elif rule.action_type == ActionType.SEND_NOTIFICATION:
            return self._action_send_notification(db, config, context)
        elif rule.action_type == ActionType.VALIDATE:
            return self._action_validate(db, config, context)
        elif rule.action_type == ActionType.UPDATE_FIELD:
            return self._action_update_field(db, config, context)
        elif rule.action_type == ActionType.CREATE_TASK:
            return self._action_create_task(db, config, context)
        elif rule.action_type == ActionType.WEBHOOK:
            return self._action_webhook(db, config, context)
        elif rule.action_type == ActionType.BLOCK:
            return self._action_block(db, config, context)
        else:
            return ActionResult(
                success=False,
                error_message=f"Unknown action type: {rule.action_type}",
            )

    def _action_send_email(
        self,
        db: Session,
        config: Dict[str, Any],
        context: TriggerContext,
    ) -> ActionResult:
        """Send an email action."""
        from app.services.email import send_email

        try:
            recipients = config.get("recipients", [])
            subject = config.get("subject", "Workflow Notification")
            body_html = config.get("body_html", "")
            body_text = config.get("body_text")

            # TODO: Template rendering with context
            # For now, simple variable substitution
            entity_id = str(context.entity_id)
            subject = subject.replace("{{entity_id}}", entity_id)
            body_html = body_html.replace("{{entity_id}}", entity_id)

            sent_to = []
            for recipient in recipients:
                if send_email(db, recipient, subject, body_html, body_text):
                    sent_to.append(recipient)

            return ActionResult(
                success=len(sent_to) > 0,
                result={"sent_to": sent_to},
            )

        except Exception as e:
            return ActionResult(
                success=False,
                error_message=str(e),
            )

    def _action_send_notification(
        self,
        db: Session,
        config: Dict[str, Any],
        context: TriggerContext,
    ) -> ActionResult:
        """Send in-app notification action."""
        # TODO: Implement notification system
        return ActionResult(
            success=True,
            result={"message": "Notification queued"},
        )

    def _action_validate(
        self,
        db: Session,
        config: Dict[str, Any],
        context: TriggerContext,
    ) -> ActionResult:
        """Validate action - check conditions and potentially block."""
        validation_rules = config.get("rules", [])
        values = context.new_values or {}

        errors = []
        for rule in validation_rules:
            field = rule.get("field")
            condition = rule.get("condition")
            message = rule.get("message", f"Validation failed for {field}")

            field_value = values.get(field)
            operator = condition.get("operator", "equals")
            expected = condition.get("value")

            if not self._compare_values(field_value, operator, expected):
                errors.append(message)

        if errors:
            return ActionResult(
                success=False,
                result={"validation_errors": errors},
                error_message="; ".join(errors),
            )

        return ActionResult(
            success=True,
            result={"validated": True},
        )

    def _action_update_field(
        self,
        db: Session,
        config: Dict[str, Any],
        context: TriggerContext,
    ) -> ActionResult:
        """Update field action."""
        # TODO: Implement field update
        field = config.get("field")
        value = config.get("value")

        return ActionResult(
            success=True,
            result={"updated_field": field, "new_value": value},
        )

    def _action_create_task(
        self,
        db: Session,
        config: Dict[str, Any],
        context: TriggerContext,
    ) -> ActionResult:
        """Create task action."""
        # TODO: Implement task creation
        return ActionResult(
            success=True,
            result={"task_created": True},
        )

    def _action_webhook(
        self,
        db: Session,
        config: Dict[str, Any],
        context: TriggerContext,
    ) -> ActionResult:
        """Webhook action."""
        import httpx

        url = config.get("url")
        is_valid, error_message = _validate_webhook_target(url, db)
        if not is_valid:
            return ActionResult(success=False, error_message=error_message)

        method = str(config.get("method", "POST")).upper()
        if method not in _ALLOWED_WEBHOOK_METHODS:
            return ActionResult(
                success=False,
                error_message=f"Webhook method '{method}' is not allowed",
            )

        raw_headers = config.get("headers", {})
        headers: dict[str, str] = {}
        if isinstance(raw_headers, dict):
            for key, value in raw_headers.items():
                key_str = str(key)
                if not _HEADER_NAME_PATTERN.match(key_str):
                    continue
                if key_str.lower() == "host":
                    continue
                value_str = str(value)
                if "\n" in value_str or "\r" in value_str:
                    continue
                headers[key_str] = value_str

        try:
            payload = {
                "entity_type": context.entity_type,
                "entity_id": str(context.entity_id),
                "event": context.event.value,
                "data": context.new_values,
            }

            timeout = httpx.Timeout(_webhook_timeout(db), connect=5.0)
            with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                response = client.request(
                    method=method,
                    url=url,
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()

            return ActionResult(
                success=True,
                result={
                    "status_code": response.status_code,
                    "content_length": response.headers.get("content-length"),
                },
            )

        except Exception as e:
            return ActionResult(
                success=False,
                error_message=str(e),
            )

    def _action_block(
        self,
        db: Session,
        config: Dict[str, Any],
        context: TriggerContext,
    ) -> ActionResult:
        """Block action - prevents the operation."""
        message = config.get("message", "Operation blocked by workflow rule")
        return ActionResult(
            success=False,
            result={"blocked": True},
            error_message=message,
        )

    def trigger_event(
        self,
        db: Session,
        organization_id: UUID,
        context: TriggerContext,
    ) -> List[WorkflowExecution]:
        """Trigger workflow evaluation for an event."""
        matching_rules = self.get_matching_rules(db, organization_id, context)
        executions = []

        for rule in matching_rules:
            execution = self.execute_action(db, rule, context)
            executions.append(execution)

            if rule.stop_on_match:
                break

        return executions

    def update_rule(
        self,
        db: Session,
        rule_id: UUID,
        updates: Dict[str, Any],
        updated_by: UUID,
    ) -> WorkflowRule:
        """Update a workflow rule."""
        rule = db.get(WorkflowRule, rule_id)
        if not rule:
            raise HTTPException(status_code=404, detail="Rule not found")

        for key, value in updates.items():
            if hasattr(rule, key):
                setattr(rule, key, value)

        rule.updated_by = updated_by
        db.flush()
        return rule

    def delete(self, db: Session, rule_id: UUID) -> bool:
        """Delete a workflow rule."""
        rule = db.get(WorkflowRule, rule_id)
        if not rule:
            return False

        db.delete(rule)
        db.flush()
        return True

    def get_executions(
        self,
        db: Session,
        rule_id: Optional[UUID] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[UUID] = None,
        status: Optional[ExecutionStatus] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[WorkflowExecution]:
        """Get workflow executions with filters."""
        query = select(WorkflowExecution)

        if rule_id:
            query = query.where(WorkflowExecution.rule_id == rule_id)
        if entity_type:
            query = query.where(WorkflowExecution.entity_type == entity_type)
        if entity_id:
            query = query.where(WorkflowExecution.entity_id == entity_id)
        if status:
            query = query.where(WorkflowExecution.status == status)

        query = query.order_by(WorkflowExecution.triggered_at.desc())
        query = query.offset(offset).limit(limit)

        return list(db.execute(query).scalars().all())


# Singleton instance
workflow_service = WorkflowService()
