"""CRUD service for service hook configuration."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.finance.platform.service_hook import (
    HookExecutionMode,
    HookHandlerType,
    ServiceHook,
)
from app.models.finance.platform.service_hook_execution import (
    ExecutionStatus,
    ServiceHookExecution,
)

logger = logging.getLogger(__name__)


class ServiceHookService:
    """Create/update/list service hooks and query execution metrics."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        organization_id: UUID | None,
        event_name: str,
        handler_type: HookHandlerType,
        handler_config: dict,
        name: str,
        execution_mode: HookExecutionMode = HookExecutionMode.ASYNC,
        conditions: dict | None = None,
        description: str | None = None,
        is_active: bool = True,
        priority: int = 10,
        max_retries: int = 3,
        retry_backoff_seconds: int = 60,
        created_by_user_id: UUID | None = None,
    ) -> ServiceHook:
        hook = ServiceHook(
            organization_id=organization_id,
            event_name=event_name,
            handler_type=handler_type,
            execution_mode=execution_mode,
            handler_config=handler_config,
            conditions=conditions or {},
            name=name,
            description=description,
            is_active=is_active,
            priority=priority,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            created_by_user_id=created_by_user_id,
        )
        self.db.add(hook)
        self.db.flush()
        logger.info("Created service hook %s (%s)", hook.hook_id, hook.event_name)
        return hook

    def update(self, hook_id: UUID, **updates: object) -> ServiceHook:
        hook = self.db.get(ServiceHook, hook_id)
        if hook is None:
            raise ValueError(f"Hook {hook_id} not found.")

        editable = {
            "name",
            "description",
            "event_name",
            "handler_type",
            "execution_mode",
            "handler_config",
            "conditions",
            "is_active",
            "priority",
            "max_retries",
            "retry_backoff_seconds",
        }
        for key, value in updates.items():
            if key in editable:
                setattr(hook, key, value)

        self.db.flush()
        logger.info("Updated service hook %s", hook.hook_id)
        return hook

    def delete(self, hook_id: UUID) -> None:
        hook = self.db.get(ServiceHook, hook_id)
        if hook is None:
            raise ValueError(f"Hook {hook_id} not found.")

        self.db.delete(hook)
        self.db.flush()
        logger.info("Deleted service hook %s", hook_id)

    def toggle(self, hook_id: UUID, is_active: bool) -> ServiceHook:
        hook = self.db.get(ServiceHook, hook_id)
        if hook is None:
            raise ValueError(f"Hook {hook_id} not found.")
        hook.is_active = is_active
        self.db.flush()
        return hook

    def bulk_toggle(
        self,
        hook_ids: Iterable[UUID],
        *,
        organization_id: UUID,
        is_active: bool,
    ) -> dict[str, object]:
        """Toggle many organization-scoped hooks in one operation."""
        unique_ids = list(dict.fromkeys(hook_ids))
        if not unique_ids:
            return {"requested": 0, "updated": 0, "not_found_ids": []}

        stmt = select(ServiceHook).where(
            ServiceHook.hook_id.in_(unique_ids),
            ServiceHook.organization_id == organization_id,
        )
        rows = list(self.db.scalars(stmt).all())
        found_ids = {row.hook_id for row in rows}
        for row in rows:
            row.is_active = is_active
        self.db.flush()
        not_found_ids = [
            str(hook_id) for hook_id in unique_ids if hook_id not in found_ids
        ]
        return {
            "requested": len(unique_ids),
            "updated": len(rows),
            "not_found_ids": not_found_ids,
        }

    def bulk_delete(
        self,
        hook_ids: Iterable[UUID],
        *,
        organization_id: UUID,
    ) -> dict[str, object]:
        """Delete many organization-scoped hooks in one operation."""
        unique_ids = list(dict.fromkeys(hook_ids))
        if not unique_ids:
            return {"requested": 0, "deleted": 0, "not_found_ids": []}

        stmt = select(ServiceHook).where(
            ServiceHook.hook_id.in_(unique_ids),
            ServiceHook.organization_id == organization_id,
        )
        rows = list(self.db.scalars(stmt).all())
        found_ids = {row.hook_id for row in rows}
        for row in rows:
            self.db.delete(row)
        self.db.flush()
        not_found_ids = [
            str(hook_id) for hook_id in unique_ids if hook_id not in found_ids
        ]
        return {
            "requested": len(unique_ids),
            "deleted": len(rows),
            "not_found_ids": not_found_ids,
        }

    def list_for_org(
        self,
        organization_id: UUID,
        *,
        event_name: str | None = None,
        handler_type: HookHandlerType | None = None,
        is_active: bool | None = None,
        name_contains: str | None = None,
    ) -> list[ServiceHook]:
        stmt = select(ServiceHook).where(
            or_(
                ServiceHook.organization_id == organization_id,
                ServiceHook.organization_id.is_(None),
            )
        )
        if event_name:
            stmt = stmt.where(ServiceHook.event_name == event_name)
        if handler_type is not None:
            stmt = stmt.where(ServiceHook.handler_type == handler_type)
        if is_active is not None:
            stmt = stmt.where(ServiceHook.is_active.is_(is_active))
        if name_contains:
            term = f"%{name_contains.strip()}%"
            stmt = stmt.where(ServiceHook.name.ilike(term))

        stmt = stmt.order_by(ServiceHook.priority.asc(), ServiceHook.created_at.asc())
        return list(self.db.scalars(stmt).all())

    def execution_stats(self, hook_id: UUID, days: int = 30) -> dict[str, int]:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        stmt = (
            select(
                ServiceHookExecution.status,
                func.count(ServiceHookExecution.execution_id),
            )
            .where(
                ServiceHookExecution.hook_id == hook_id,
                ServiceHookExecution.created_at >= cutoff,
            )
            .group_by(ServiceHookExecution.status)
        )
        rows = self.db.execute(stmt).all()

        stats = {status.value: 0 for status in ExecutionStatus}
        for status, count in rows:
            if isinstance(status, ExecutionStatus):
                stats[status.value] = int(count)
        return stats

    def list_executions(
        self,
        hook_id: UUID,
        *,
        status: ExecutionStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ServiceHookExecution]:
        stmt = select(ServiceHookExecution).where(
            ServiceHookExecution.hook_id == hook_id
        )
        if status is not None:
            stmt = stmt.where(ServiceHookExecution.status == status)
        stmt = stmt.order_by(ServiceHookExecution.created_at.desc())
        stmt = stmt.limit(limit).offset(offset)
        return list(self.db.scalars(stmt).all())

    def count_executions(
        self,
        hook_id: UUID,
        *,
        status: ExecutionStatus | None = None,
    ) -> int:
        """Return total matching executions for pagination."""
        stmt = select(func.count(ServiceHookExecution.execution_id)).where(
            ServiceHookExecution.hook_id == hook_id
        )
        if status is not None:
            stmt = stmt.where(ServiceHookExecution.status == status)
        total = self.db.scalar(stmt)
        return int(total or 0)

    def get_execution(
        self,
        hook_id: UUID,
        execution_id: UUID,
        *,
        organization_id: UUID,
    ) -> ServiceHookExecution:
        """Return a single execution visible to the tenant."""
        hook = self.db.get(ServiceHook, hook_id)
        if hook is None or hook.organization_id not in {None, organization_id}:
            raise ValueError("Hook not found.")

        execution = self.db.get(ServiceHookExecution, execution_id)
        if execution is None or execution.hook_id != hook_id:
            raise ValueError("Execution not found.")
        if execution.organization_id != organization_id:
            raise ValueError("Execution not found.")
        return execution

    def retry_execution(
        self,
        hook_id: UUID,
        execution_id: UUID,
        *,
        organization_id: UUID,
    ) -> ServiceHookExecution:
        hook = self.db.get(ServiceHook, hook_id)
        if hook is None or hook.organization_id not in {None, organization_id}:
            raise ValueError("Hook not found.")

        execution = self.db.get(ServiceHookExecution, execution_id)
        if execution is None or execution.hook_id != hook_id:
            raise ValueError("Execution not found.")
        if execution.organization_id != organization_id:
            raise ValueError("Execution not found.")
        if execution.status not in {ExecutionStatus.FAILED, ExecutionStatus.DEAD}:
            raise ValueError("Only FAILED or DEAD executions can be retried.")

        from app.tasks.hooks import execute_async_hook

        execution.status = ExecutionStatus.PENDING
        execution.error_message = None
        execution.executed_at = None
        execution.duration_ms = None
        self.db.flush()
        execute_async_hook.delay(
            execution_id=str(execution.execution_id),
            hook_id=str(hook.hook_id),
        )
        return execution
