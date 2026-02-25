"""Web-facing helpers for service hook management."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.finance.platform.service_hook import (
    HookExecutionMode,
    HookHandlerType,
    ServiceHook,
)
from app.models.finance.platform.service_hook_execution import ServiceHookExecution
from app.services.common import coerce_uuid
from app.services.feature_flags import FEATURE_SERVICE_HOOKS, is_feature_enabled
from app.services.hooks import events as hook_events
from app.services.hooks.service_hook import ServiceHookService

logger = logging.getLogger(__name__)


class ServiceHookWebService:
    """Build context and perform web mutations for Service Hooks."""

    @staticmethod
    def _parse_form_values(
        data: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str | None]:
        name = (data.get("name") or "").strip()
        event_name = (data.get("event_name") or "").strip()
        handler_type_raw = (data.get("handler_type") or "").strip().upper()
        execution_mode_raw = (data.get("execution_mode") or "ASYNC").strip().upper()

        if not name:
            return None, "Name is required."
        if not event_name:
            return None, "Event name is required."

        try:
            handler_type = HookHandlerType(handler_type_raw)
        except ValueError:
            return None, "Invalid handler type."

        try:
            execution_mode = HookExecutionMode(execution_mode_raw)
        except ValueError:
            return None, "Invalid execution mode."

        handler_config: dict[str, Any] = {}
        if handler_type == HookHandlerType.WEBHOOK:
            webhook_url = (data.get("webhook_url") or "").strip()
            if not webhook_url:
                return None, "Webhook URL is required for WEBHOOK handler."
            handler_config["url"] = webhook_url
            handler_config["method"] = (
                data.get("webhook_method") or "POST"
            ).strip().upper() or "POST"
            timeout_raw = (data.get("webhook_timeout_seconds") or "").strip()
            if timeout_raw:
                try:
                    timeout_seconds = int(timeout_raw)
                except ValueError:
                    return None, "Webhook timeout must be a number."
                if timeout_seconds < 1:
                    return None, "Webhook timeout must be at least 1 second."
                handler_config["timeout_seconds"] = timeout_seconds
        elif handler_type == HookHandlerType.EVENT_OUTBOX:
            override_name = (data.get("event_name_override") or "").strip()
            if override_name:
                handler_config["event_name_override"] = override_name

        try:
            priority = int(str(data.get("priority") or "10"))
            max_retries = int(str(data.get("max_retries") or "3"))
            backoff = int(str(data.get("retry_backoff_seconds") or "60"))
        except ValueError:
            return None, "Priority, max retries, and backoff must be numbers."
        circuit_breaker_raw = (data.get("circuit_breaker_failures") or "").strip()
        if circuit_breaker_raw:
            try:
                circuit_breaker_failures = int(circuit_breaker_raw)
            except ValueError:
                return None, "Circuit breaker failures must be a number."
            if circuit_breaker_failures < 0:
                return None, "Circuit breaker failures cannot be negative."
            if circuit_breaker_failures > 0:
                handler_config["circuit_breaker_failures"] = circuit_breaker_failures

        is_active = str(data.get("is_active") or "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

        return (
            {
                "name": name,
                "event_name": event_name,
                "handler_type": handler_type,
                "execution_mode": execution_mode,
                "handler_config": handler_config,
                "description": (data.get("description") or "").strip() or None,
                "is_active": is_active,
                "priority": priority,
                "max_retries": max_retries,
                "retry_backoff_seconds": backoff,
            },
            None,
        )

    @staticmethod
    def settings_context(db: Session, organization_id: UUID) -> dict[str, Any]:
        return ServiceHookWebService.settings_context_filtered(
            db,
            organization_id,
            q=None,
            handler_type=None,
            is_active=None,
        )

    @staticmethod
    def settings_context_filtered(
        db: Session,
        organization_id: UUID,
        *,
        q: str | None,
        handler_type: str | None,
        is_active: str | None,
    ) -> dict[str, Any]:
        service = ServiceHookService(db)
        handler_enum = None
        if handler_type:
            try:
                handler_enum = HookHandlerType(handler_type)
            except ValueError:
                handler_enum = None
        is_active_filter = None
        if is_active == "true":
            is_active_filter = True
        elif is_active == "false":
            is_active_filter = False
        hooks = service.list_for_org(
            organization_id,
            handler_type=handler_enum,
            is_active=is_active_filter,
            name_contains=q,
        )

        hook_items = []
        for hook in hooks:
            config = hook.handler_config or {}
            stats = service.execution_stats(hook.hook_id, days=30)
            executions = service.list_executions(hook.hook_id, limit=5)
            execution_items = [
                ServiceHookWebService._to_execution_item(execution)
                for execution in executions
            ]
            hook_items.append(
                {
                    "hook_id": str(hook.hook_id),
                    "name": hook.name,
                    "event_name": hook.event_name,
                    "handler_type": hook.handler_type.value,
                    "execution_mode": hook.execution_mode.value,
                    "is_active": hook.is_active,
                    "priority": hook.priority,
                    "max_retries": hook.max_retries,
                    "retry_backoff_seconds": hook.retry_backoff_seconds,
                    "description": hook.description,
                    "webhook_url": config.get("url"),
                    "webhook_method": config.get("method", "POST"),
                    "event_name_override": config.get("event_name_override"),
                    "webhook_timeout_seconds": config.get("timeout_seconds"),
                    "circuit_breaker_failures": config.get(
                        "circuit_breaker_failures"
                    ),
                    "stats_success": int(stats.get("SUCCESS", 0)),
                    "stats_failed": int(stats.get("FAILED", 0)),
                    "stats_dead": int(stats.get("DEAD", 0)),
                    "recent_executions": execution_items,
                }
            )

        available_events = sorted(
            value
            for value in (getattr(hook_events, name) for name in hook_events.__all__)
            if isinstance(value, str)
        )

        return {
            "service_hooks_enabled": is_feature_enabled(db, FEATURE_SERVICE_HOOKS),
            "hooks": hook_items,
            "available_events": available_events,
            "handler_types": [member.value for member in HookHandlerType],
            "execution_modes": [member.value for member in HookExecutionMode],
            "filters": {
                "q": q or "",
                "handler_type": handler_type or "",
                "is_active": is_active or "",
            },
        }

    @staticmethod
    def create_from_form(
        db: Session,
        organization_id: UUID,
        created_by_user_id: UUID | None,
        data: dict[str, Any],
    ) -> tuple[bool, str | None]:
        parsed, error = ServiceHookWebService._parse_form_values(data)
        if parsed is None:
            return False, error

        service = ServiceHookService(db)
        service.create(
            organization_id=organization_id,
            event_name=parsed["event_name"],
            handler_type=parsed["handler_type"],
            execution_mode=parsed["execution_mode"],
            handler_config=parsed["handler_config"],
            name=parsed["name"],
            description=parsed["description"],
            is_active=parsed["is_active"],
            priority=parsed["priority"],
            max_retries=parsed["max_retries"],
            retry_backoff_seconds=parsed["retry_backoff_seconds"],
            created_by_user_id=created_by_user_id,
        )
        db.commit()
        return True, None

    @staticmethod
    def _to_execution_item(execution: ServiceHookExecution) -> dict[str, Any]:
        return {
            "execution_id": str(execution.execution_id),
            "status": execution.status.value,
            "event_name": execution.event_name,
            "duration_ms": execution.duration_ms,
            "response_status_code": execution.response_status_code,
            "retry_count": execution.retry_count,
            "error_message": execution.error_message,
            "created_at": execution.created_at.isoformat()
            if execution.created_at
            else None,
            "executed_at": execution.executed_at.isoformat()
            if execution.executed_at
            else None,
        }

    @staticmethod
    def execution_detail(
        db: Session,
        organization_id: UUID,
        hook_id: str,
        execution_id: str,
    ) -> tuple[dict[str, Any] | None, str | None]:
        service = ServiceHookService(db)
        try:
            execution = service.get_execution(
                coerce_uuid(hook_id),
                coerce_uuid(execution_id),
                organization_id=organization_id,
            )
        except Exception as exc:
            return None, str(exc)

        item = ServiceHookWebService._to_execution_item(execution)
        item["event_payload"] = execution.event_payload or {}
        item["response_body"] = execution.response_body
        return item, None

    @staticmethod
    def update_from_form(
        db: Session,
        organization_id: UUID,
        hook_id: str,
        data: dict[str, Any],
    ) -> tuple[bool, str | None]:
        parsed, error = ServiceHookWebService._parse_form_values(data)
        if parsed is None:
            return False, error

        service = ServiceHookService(db)
        try:
            hook_uuid = coerce_uuid(hook_id)
            hook = db.get(ServiceHook, hook_uuid)
            if hook is None or hook.organization_id != organization_id:
                return False, "Hook not found."
            service.update(
                hook_uuid,
                name=parsed["name"],
                event_name=parsed["event_name"],
                handler_type=parsed["handler_type"],
                execution_mode=parsed["execution_mode"],
                handler_config=parsed["handler_config"],
                description=parsed["description"],
                is_active=parsed["is_active"],
                priority=parsed["priority"],
                max_retries=parsed["max_retries"],
                retry_backoff_seconds=parsed["retry_backoff_seconds"],
            )
            db.commit()
            return True, None
        except Exception as exc:
            return False, str(exc)

    @staticmethod
    def toggle(
        db: Session,
        organization_id: UUID,
        hook_id: str,
        enabled: bool,
    ) -> tuple[bool, str | None]:
        service = ServiceHookService(db)
        try:
            hook_uuid = coerce_uuid(hook_id)
            hook = db.get(ServiceHook, hook_uuid)
            if hook is None or hook.organization_id != organization_id:
                return False, "Hook not found."
            service.toggle(hook_uuid, enabled)
            db.commit()
            return True, None
        except Exception as exc:
            return False, str(exc)

    @staticmethod
    def bulk_toggle(
        db: Session,
        organization_id: UUID,
        hook_ids: list[str],
        *,
        enabled: bool,
    ) -> tuple[bool, str | None, dict[str, object] | None]:
        service = ServiceHookService(db)
        try:
            parsed_ids = [coerce_uuid(hook_id) for hook_id in hook_ids]
            result = service.bulk_toggle(
                parsed_ids,
                organization_id=organization_id,
                is_active=enabled,
            )
            db.commit()
            return True, None, result
        except Exception as exc:
            return False, str(exc), None

    @staticmethod
    def bulk_delete(
        db: Session,
        organization_id: UUID,
        hook_ids: list[str],
    ) -> tuple[bool, str | None, dict[str, object] | None]:
        service = ServiceHookService(db)
        try:
            parsed_ids = [coerce_uuid(hook_id) for hook_id in hook_ids]
            result = service.bulk_delete(
                parsed_ids,
                organization_id=organization_id,
            )
            db.commit()
            return True, None, result
        except Exception as exc:
            return False, str(exc), None

    @staticmethod
    def delete(
        db: Session,
        organization_id: UUID,
        hook_id: str,
    ) -> tuple[bool, str | None]:
        service = ServiceHookService(db)
        try:
            hook_uuid = coerce_uuid(hook_id)
            hook = db.get(ServiceHook, hook_uuid)
            if hook is None or hook.organization_id != organization_id:
                return False, "Hook not found."
            service.delete(hook_uuid)
            db.commit()
            return True, None
        except Exception as exc:
            return False, str(exc)

    @staticmethod
    def retry_execution(
        db: Session,
        organization_id: UUID,
        hook_id: str,
        execution_id: str,
    ) -> tuple[bool, str | None]:
        service = ServiceHookService(db)
        try:
            hook_uuid = coerce_uuid(hook_id)
            hook = db.get(ServiceHook, hook_uuid)
            if hook is None or hook.organization_id not in {None, organization_id}:
                return False, "Hook not found."
            service.retry_execution(
                hook_uuid,
                coerce_uuid(execution_id),
                organization_id=organization_id,
            )
            db.commit()
            return True, None
        except Exception as exc:
            return False, str(exc)


service_hook_web_service = ServiceHookWebService()
