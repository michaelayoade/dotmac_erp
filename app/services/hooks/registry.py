"""Service hook event registry and dispatcher."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from string import Template
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.finance.platform.event_outbox import EventOutbox, EventStatus
from app.models.finance.platform.service_hook import (
    HookExecutionMode,
    HookHandlerType,
    ServiceHook,
)
from app.models.finance.platform.service_hook_execution import (
    ExecutionStatus,
    ServiceHookExecution,
)
from app.services.feature_flags import FEATURE_SERVICE_HOOKS, is_feature_enabled

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HookEvent:
    """Event payload emitted by a domain service."""

    event_name: str
    organization_id: UUID
    entity_type: str
    entity_id: UUID
    actor_user_id: UUID | None
    payload: dict[str, Any]


class HookRegistry:
    """Dispatch domain events to registered service hooks."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def emit(self, event: HookEvent) -> list[UUID]:
        """Emit an event to matching hooks and return execution IDs."""
        if not is_feature_enabled(self.db, event.organization_id, FEATURE_SERVICE_HOOKS):
            return []

        hooks = self._find_matching_hooks(event)
        if not hooks:
            return []

        execution_ids: list[UUID] = []
        for hook in hooks:
            if not self._conditions_match(hook.conditions or {}, event.payload):
                continue

            execution = ServiceHookExecution(
                hook_id=hook.hook_id,
                organization_id=event.organization_id,
                event_name=event.event_name,
                event_payload={
                    **event.payload,
                    "_hook_meta": {
                        "entity_type": event.entity_type,
                        "entity_id": str(event.entity_id),
                        "actor_user_id": str(event.actor_user_id)
                        if event.actor_user_id
                        else None,
                    },
                },
                status=ExecutionStatus.PENDING,
            )
            self.db.add(execution)
            self.db.flush()
            execution_ids.append(execution.execution_id)

            if hook.execution_mode == HookExecutionMode.SYNC:
                self._execute_sync(hook, execution, event)
            else:
                from app.tasks.hooks import execute_async_hook

                execute_async_hook.delay(
                    execution_id=str(execution.execution_id),
                    hook_id=str(hook.hook_id),
                )

        logger.info(
            "Hook event emitted: event=%s hooks=%d executions=%d",
            event.event_name,
            len(hooks),
            len(execution_ids),
        )
        return execution_ids

    def _find_matching_hooks(self, event: HookEvent) -> list[ServiceHook]:
        stmt = (
            select(ServiceHook)
            .where(
                ServiceHook.event_name == event.event_name,
                ServiceHook.is_active.is_(True),
                or_(
                    ServiceHook.organization_id == event.organization_id,
                    ServiceHook.organization_id.is_(None),
                ),
            )
            .order_by(ServiceHook.priority.asc(), ServiceHook.created_at.asc())
        )
        return list(self.db.scalars(stmt).all())

    @staticmethod
    def _conditions_match(conditions: dict[str, Any], payload: dict[str, Any]) -> bool:
        if not conditions:
            return True
        for key, expected in conditions.items():
            if key.endswith("_gt"):
                field = key[:-3]
                if payload.get(field, 0) <= expected:
                    return False
                continue
            if key.endswith("_gte"):
                field = key[:-4]
                if payload.get(field, 0) < expected:
                    return False
                continue
            if key.endswith("_lt"):
                field = key[:-3]
                if payload.get(field, 0) >= expected:
                    return False
                continue
            if key.endswith("_lte"):
                field = key[:-4]
                if payload.get(field, 0) > expected:
                    return False
                continue
            if key.endswith("_in"):
                field = key[:-3]
                if payload.get(field) not in expected:
                    return False
                continue
            if payload.get(key) != expected:
                return False
        return True

    def _execute_sync(
        self,
        hook: ServiceHook,
        execution: ServiceHookExecution,
        event: HookEvent,
    ) -> None:
        started = time.monotonic()
        try:
            result = _execute_hook_handler(self.db, hook, event)
            execution.status = ExecutionStatus.SUCCESS
            execution.response_body = str(result)[:1000]
        except Exception as exc:
            execution.status = ExecutionStatus.FAILED
            execution.error_message = str(exc)[:500]
            logger.exception("Sync hook failed: hook=%s", hook.hook_id)
        finally:
            execution.duration_ms = int((time.monotonic() - started) * 1000)
            execution.executed_at = datetime.now(UTC)


def _validate_webhook_target(
    url: str,
    db: Session,
    *,
    allow_localhost: bool = False,
) -> tuple[bool, str | None]:
    """Lazy-import wrapper to avoid circular import with workflow module."""
    from app.services.finance.automation.workflow import (
        _validate_webhook_target as _inner,
    )

    return _inner(url, db, allow_localhost=allow_localhost)


def _execute_hook_handler(
    db: Session,
    hook: ServiceHook,
    event: HookEvent,
) -> dict[str, Any]:
    """Execute the configured handler and return a compact result payload."""
    payload_context = {
        **event.payload,
        "event_name": event.event_name,
        "organization_id": str(event.organization_id),
        "entity_type": event.entity_type,
        "entity_id": str(event.entity_id),
        "actor_user_id": str(event.actor_user_id) if event.actor_user_id else "",
    }

    if hook.handler_type == HookHandlerType.NOTIFICATION:
        from app.models.notification import (
            EntityType,
            NotificationChannel,
            NotificationType,
        )
        from app.services.notification import NotificationService

        recipient_raw = hook.handler_config.get("recipient_id")
        if not recipient_raw:
            raise ValueError("Notification hook requires handler_config.recipient_id")

        try:
            recipient_id = UUID(str(recipient_raw))
        except (TypeError, ValueError) as exc:
            raise ValueError("Notification hook recipient_id must be a UUID") from exc

        entity_type_raw = str(
            hook.handler_config.get("entity_type", EntityType.SYSTEM.value)
        ).upper()
        notification_type_raw = str(
            hook.handler_config.get("notification_type", NotificationType.INFO.value)
        ).upper()
        channel_raw = str(
            hook.handler_config.get("channel", NotificationChannel.IN_APP.value)
        ).upper()

        try:
            entity_type = EntityType(entity_type_raw)
            notification_type = NotificationType(notification_type_raw)
            channel = NotificationChannel(channel_raw)
        except ValueError as exc:
            raise ValueError(
                "Invalid notification enum value in handler_config"
            ) from exc

        title_template = str(hook.handler_config.get("title", event.event_name))
        message_template = str(
            hook.handler_config.get("message", "Service hook notification")
        )
        action_url_template = hook.handler_config.get("action_url")

        title = Template(title_template).safe_substitute(payload_context)
        message = Template(message_template).safe_substitute(payload_context)
        action_url = (
            Template(str(action_url_template)).safe_substitute(payload_context)
            if action_url_template
            else None
        )

        entity_id_raw = hook.handler_config.get("entity_id")
        if entity_id_raw:
            entity_id = UUID(str(entity_id_raw))
        else:
            entity_id = event.entity_id

        notification = NotificationService().create(
            db,
            organization_id=event.organization_id,
            recipient_id=recipient_id,
            entity_type=entity_type,
            entity_id=entity_id,
            notification_type=notification_type,
            title=title,
            message=message,
            channel=channel,
            action_url=action_url,
            actor_id=event.actor_user_id,
        )
        return {"notification_id": str(notification.notification_id)}

    if hook.handler_type == HookHandlerType.EMAIL:
        from app.services.email import send_email

        to_email = str(hook.handler_config.get("to_email") or "").strip()
        if not to_email:
            raise ValueError("Email hook requires handler_config.to_email")

        subject_template = str(hook.handler_config.get("subject", event.event_name))
        body_html_template = str(
            hook.handler_config.get("body_html", "<p>Service hook event</p>")
        )
        body_text_template = hook.handler_config.get("body_text")

        subject = Template(subject_template).safe_substitute(payload_context)
        body_html = Template(body_html_template).safe_substitute(payload_context)
        body_text = (
            Template(str(body_text_template)).safe_substitute(payload_context)
            if body_text_template
            else None
        )

        module_name = hook.handler_config.get("email_module")
        email_module = None
        if module_name:
            from app.services.email import EmailModule

            email_module = EmailModule(str(module_name).upper())

        sent = send_email(
            db=db,
            to_email=to_email,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            module=email_module,
            organization_id=event.organization_id,
        )
        if not sent:
            raise ValueError("Failed to send email notification")
        return {"sent": True, "to_email": to_email}

    if hook.handler_type == HookHandlerType.INTERNAL_SERVICE:
        target = str(hook.handler_config.get("target") or "").strip()
        if not target:
            raise ValueError("Internal service hook requires handler_config.target")
        if not target.startswith("app.services."):
            raise ValueError("Internal service target must be inside app.services")
        if ":" not in target:
            raise ValueError(
                "Internal service target must use 'module.path:callable_name'"
            )

        module_path, callable_name = target.split(":", 1)
        target_module = import_module(module_path)
        callback = getattr(target_module, callable_name, None)
        if callback is None or not callable(callback):
            raise ValueError(f"Internal service target not callable: {target}")

        kwargs = dict(hook.handler_config.get("kwargs") or {})
        result = callback(
            db=db,
            event=event,
            hook=hook,
            **kwargs,
        )
        return {"result": str(result)[:1000]}

    if hook.handler_type == HookHandlerType.EVENT_OUTBOX:
        entry = EventOutbox(
            event_name=hook.handler_config.get("event_name_override", event.event_name),
            producer_module="hooks",
            aggregate_type=event.entity_type,
            aggregate_id=str(event.entity_id),
            correlation_id=f"hook:{hook.hook_id}:{event.entity_id}",
            idempotency_key=f"hook:{hook.hook_id}:{event.entity_id}:{event.event_name}",
            payload=event.payload,
            headers={
                "organization_id": str(event.organization_id),
                "user_id": str(event.actor_user_id) if event.actor_user_id else None,
                "source": "service_hook",
            },
            status=EventStatus.PENDING,
        )
        db.add(entry)
        db.flush()
        return {"outbox_event_id": str(entry.event_id)}

    if hook.handler_type == HookHandlerType.WEBHOOK:
        import httpx

        url_value = hook.handler_config.get("url")
        url = "" if url_value is None else str(url_value).strip()
        if not url:
            raise ValueError("Webhook hook requires handler_config.url")

        is_valid, error_message = _validate_webhook_target(
            url,
            db,
            allow_localhost=False,
        )
        if not is_valid:
            raise ValueError(error_message or "Webhook target is not allowed")

        method = str(hook.handler_config.get("method", "POST")).upper()
        timeout_s = float(hook.handler_config.get("timeout_seconds", 15))
        headers = dict(hook.handler_config.get("headers", {}))
        body = {
            "event": event.event_name,
            "organization_id": str(event.organization_id),
            "entity_type": event.entity_type,
            "entity_id": str(event.entity_id),
            "payload": event.payload,
        }

        with httpx.Client(timeout=timeout_s, follow_redirects=False) as client:
            if method == "POST":
                response = client.post(url, json=body, headers=headers)
            elif method == "PUT":
                response = client.put(url, json=body, headers=headers)
            else:
                raise ValueError(f"Unsupported webhook method: {method}")

        response.raise_for_status()
        return {
            "status_code": response.status_code,
            "body": response.text[:1000],
        }

    raise ValueError(f"Unsupported hook handler type: {hook.handler_type.value}")


def emit_hook_event(
    db: Session,
    *,
    event_name: str,
    organization_id: UUID,
    entity_type: str,
    entity_id: UUID,
    actor_user_id: UUID | None,
    payload: dict[str, Any] | None = None,
) -> list[UUID]:
    """Convenience wrapper for emitting hook events from domain services."""
    registry = HookRegistry(db)
    return registry.emit(
        HookEvent(
            event_name=event_name,
            organization_id=organization_id,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_user_id=actor_user_id,
            payload=payload or {},
        )
    )
