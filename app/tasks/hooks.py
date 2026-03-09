"""Async service hook execution tasks."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from celery import shared_task
from sqlalchemy import select

from app.db import SessionLocal
from app.models.finance.platform.service_hook import HookHandlerType, ServiceHook
from app.models.finance.platform.service_hook_execution import (
    ExecutionStatus,
    ServiceHookExecution,
)
from app.services.hooks.registry import HookEvent, _execute_hook_handler

logger = logging.getLogger(__name__)
MAX_RETRY_BACKOFF_SECONDS = 3600
TERMINAL_FAILURE_STATUSES = {ExecutionStatus.FAILED, ExecutionStatus.DEAD}


def _compute_retry_countdown(base_seconds: int, retry_count: int) -> int:
    """Return bounded exponential backoff countdown in seconds."""
    safe_base = max(1, base_seconds)
    safe_retry_count = max(1, retry_count)
    countdown = safe_base * (2 ** (safe_retry_count - 1))
    return int(min(countdown, MAX_RETRY_BACKOFF_SECONDS))


def _trip_circuit_breaker_if_needed(db, hook: ServiceHook) -> bool:
    """Disable a hook if recent executions indicate sustained failures."""
    threshold_raw = hook.handler_config.get("circuit_breaker_failures")
    if threshold_raw is None or isinstance(threshold_raw, bool):
        return False
    try:
        threshold = int(str(threshold_raw))
    except (TypeError, ValueError):
        return False
    if threshold <= 0:
        return False

    stmt = (
        select(ServiceHookExecution.status)
        .where(ServiceHookExecution.hook_id == hook.hook_id)
        .order_by(ServiceHookExecution.created_at.desc())
        .limit(threshold)
    )
    recent_statuses = list(db.scalars(stmt).all())
    if len(recent_statuses) < threshold:
        return False
    if not all(status in TERMINAL_FAILURE_STATUSES for status in recent_statuses):
        return False

    hook.is_active = False
    return True


def _should_retry_hook_failure(hook: ServiceHook, exc: Exception) -> bool:
    """Return whether a hook failure should be retried."""
    if hook.handler_type != HookHandlerType.WEBHOOK:
        return True

    import httpx

    if isinstance(exc, (httpx.TimeoutException, httpx.RequestError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else None
        return bool(status and (status >= 500 or status == 429))
    return False


@shared_task
def cleanup_old_hook_executions(
    retention_days: int = 90,
    batch_size: int = 5000,
    organization_id: UUID | str | None = None,
) -> dict[str, Any]:
    """Delete old service hook execution logs beyond retention period."""
    if retention_days < 1:
        raise ValueError("retention_days must be >= 1")
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")

    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    deleted = 0
    errors: list[str] = []
    org_id = UUID(str(organization_id)) if organization_id is not None else None

    with SessionLocal() as db:
        try:
            stmt = (
                select(ServiceHookExecution.execution_id)
                .where(ServiceHookExecution.created_at < cutoff)
                .order_by(ServiceHookExecution.created_at.asc())
                .limit(batch_size)
            )
            if org_id is not None:
                stmt = stmt.where(ServiceHookExecution.organization_id == org_id)

            execution_ids = list(db.scalars(stmt).all())
            if not execution_ids:
                return {"deleted": 0, "errors": []}

            from sqlalchemy import delete

            result = db.execute(
                delete(ServiceHookExecution).where(
                    ServiceHookExecution.execution_id.in_(execution_ids)
                )
            )
            deleted = result.rowcount
            db.commit()
        except Exception as exc:
            db.rollback()
            errors.append(str(exc))
            logger.exception("Failed to cleanup old hook executions")

    return {"deleted": deleted, "errors": errors}


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def execute_async_hook(
    self,
    execution_id: str,
    hook_id: str,
) -> dict[str, Any]:
    """Execute queued service hook and persist execution result."""
    with SessionLocal() as db:
        execution = db.get(ServiceHookExecution, UUID(execution_id))
        hook = db.get(ServiceHook, UUID(hook_id))

        if execution is None or hook is None:
            logger.error(
                "Async hook references missing entities (execution=%s, hook=%s)",
                execution_id,
                hook_id,
            )
            return {"ok": False, "error": "missing entities"}

        started = time.monotonic()
        payload = dict(execution.event_payload or {})
        meta = payload.pop("_hook_meta", {}) if isinstance(payload, dict) else {}

        org_id = execution.organization_id or hook.organization_id
        if org_id is None:
            logger.error("Async hook missing organization context: %s", execution_id)
            execution.status = ExecutionStatus.DEAD
            execution.error_message = "Missing organization context"
            db.flush()
            db.commit()
            return {"ok": False, "error": "missing organization context"}

        entity_type = str(meta.get("entity_type") or "Unknown")
        entity_id = (
            UUID(str(meta.get("entity_id"))) if meta.get("entity_id") else org_id
        )
        actor_user_id = (
            UUID(str(meta.get("actor_user_id"))) if meta.get("actor_user_id") else None
        )

        event = HookEvent(
            event_name=execution.event_name,
            organization_id=org_id,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_user_id=actor_user_id,
            payload=payload if isinstance(payload, dict) else {},
        )

        try:
            result = _execute_hook_handler(db, hook, event)
            execution.status = ExecutionStatus.SUCCESS
            execution.response_body = str(result)[:1000]
            status_code = (
                result.get("status_code") if isinstance(result, dict) else None
            )
            if isinstance(status_code, (int, str)):
                execution.response_status_code = int(status_code)
            else:
                execution.response_status_code = None
            db.flush()
            db.commit()
            return {"ok": True, "execution_id": execution_id}
        except Exception as exc:
            execution.retry_count = (execution.retry_count or 0) + 1
            retryable = _should_retry_hook_failure(hook, exc)
            if retryable and execution.retry_count < max(1, hook.max_retries):
                execution.status = ExecutionStatus.RETRYING
            elif retryable:
                execution.status = ExecutionStatus.DEAD
            else:
                execution.status = ExecutionStatus.FAILED
            execution.error_message = str(exc)[:500]
            execution.duration_ms = int((time.monotonic() - started) * 1000)
            execution.executed_at = datetime.now(UTC)
            db.flush()
            db.commit()

            if execution.status == ExecutionStatus.RETRYING:
                countdown = _compute_retry_countdown(
                    base_seconds=hook.retry_backoff_seconds,
                    retry_count=execution.retry_count,
                )
                raise self.retry(exc=exc, countdown=countdown)

            if _trip_circuit_breaker_if_needed(db, hook):
                logger.error(
                    "Hook disabled by circuit breaker (hook=%s threshold=%s)",
                    hook.hook_id,
                    hook.handler_config.get("circuit_breaker_failures"),
                )

            logger.exception(
                "Async hook failed permanently (execution=%s)", execution_id
            )
            return {"ok": False, "execution_id": execution_id, "error": str(exc)}
        finally:
            if execution.duration_ms is None:
                execution.duration_ms = int((time.monotonic() - started) * 1000)
            if execution.executed_at is None:
                execution.executed_at = datetime.now(UTC)
            db.flush()
            db.commit()
