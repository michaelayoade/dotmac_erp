"""Service Hook management API endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.db import SessionLocal
from app.models.finance.platform.service_hook import (
    HookExecutionMode,
    HookHandlerType,
    ServiceHook,
)
from app.models.finance.platform.service_hook_execution import (
    ExecutionStatus,
    ServiceHookExecution,
)
from app.schemas.finance.common import ListResponse
from app.services.common import coerce_uuid
from app.services.hooks.service_hook import ServiceHookService

router = APIRouter(
    prefix="/service-hooks",
    tags=["service-hooks"],
    dependencies=[Depends(require_tenant_auth)],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class ServiceHookCreate(BaseModel):
    """Request body for creating a service hook."""

    event_name: str = Field(min_length=1, max_length=100)
    handler_type: HookHandlerType
    name: str = Field(min_length=1, max_length=200)
    execution_mode: HookExecutionMode = HookExecutionMode.ASYNC
    handler_config: dict = Field(default_factory=dict)
    conditions: dict = Field(default_factory=dict)
    description: str | None = None
    is_active: bool = True
    priority: int = 10
    max_retries: int = 3
    retry_backoff_seconds: int = 60
    circuit_breaker_failures: int | None = Field(default=None, ge=0, le=100)
    webhook_timeout_seconds: int | None = Field(default=None, ge=1, le=300)


class ServiceHookUpdate(BaseModel):
    """Request body for updating a service hook."""

    event_name: str | None = Field(default=None, min_length=1, max_length=100)
    handler_type: HookHandlerType | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    execution_mode: HookExecutionMode | None = None
    handler_config: dict | None = None
    conditions: dict | None = None
    description: str | None = None
    is_active: bool | None = None
    priority: int | None = None
    max_retries: int | None = None
    retry_backoff_seconds: int | None = None
    circuit_breaker_failures: int | None = Field(default=None, ge=0, le=100)
    webhook_timeout_seconds: int | None = Field(default=None, ge=1, le=300)


class ServiceHookToggle(BaseModel):
    """Request body for toggling hook enabled state."""

    enabled: bool


class ServiceHookBulkToggle(BaseModel):
    """Bulk toggle payload."""

    hook_ids: list[UUID] = Field(min_length=1, max_length=500)
    enabled: bool


class ServiceHookBulkDelete(BaseModel):
    """Bulk delete payload."""

    hook_ids: list[UUID] = Field(min_length=1, max_length=500)


class ServiceHookBulkResult(BaseModel):
    """Bulk mutation summary."""

    requested: int
    processed: int
    not_found_ids: list[str] = Field(default_factory=list)


class ServiceHookRead(BaseModel):
    """Service hook response schema."""

    model_config = ConfigDict(from_attributes=True)

    hook_id: UUID
    organization_id: UUID | None
    event_name: str
    handler_type: HookHandlerType
    execution_mode: HookExecutionMode
    handler_config: dict
    conditions: dict
    name: str
    description: str | None
    is_active: bool
    priority: int
    max_retries: int
    retry_backoff_seconds: int
    circuit_breaker_failures: int | None = None
    webhook_timeout_seconds: int | None = None
    created_by_user_id: UUID | None

    @classmethod
    def from_model(cls, hook: ServiceHook) -> ServiceHookRead:
        config = hook.handler_config or {}
        circuit_breaker_failures = config.get("circuit_breaker_failures")
        webhook_timeout_seconds = config.get("timeout_seconds")
        try:
            cb_value = (
                int(circuit_breaker_failures)
                if circuit_breaker_failures is not None
                else None
            )
        except (TypeError, ValueError):
            cb_value = None
        try:
            timeout_value = (
                int(webhook_timeout_seconds)
                if webhook_timeout_seconds is not None
                else None
            )
        except (TypeError, ValueError):
            timeout_value = None

        return cls(
            hook_id=hook.hook_id,
            organization_id=hook.organization_id,
            event_name=hook.event_name,
            handler_type=hook.handler_type,
            execution_mode=hook.execution_mode,
            handler_config=config,
            conditions=hook.conditions or {},
            name=hook.name,
            description=hook.description,
            is_active=hook.is_active,
            priority=hook.priority,
            max_retries=hook.max_retries,
            retry_backoff_seconds=hook.retry_backoff_seconds,
            circuit_breaker_failures=cb_value,
            webhook_timeout_seconds=timeout_value,
            created_by_user_id=hook.created_by_user_id,
        )


class ServiceHookStatsRead(BaseModel):
    """Execution stats response schema."""

    hook_id: UUID
    days: int
    stats: dict[str, int]


class ServiceHookExecutionRead(BaseModel):
    """Service hook execution response schema."""

    model_config = ConfigDict(from_attributes=True)

    execution_id: UUID
    hook_id: UUID
    organization_id: UUID | None
    event_name: str
    status: ExecutionStatus
    response_status_code: int | None
    error_message: str | None
    retry_count: int
    duration_ms: int | None
    created_at: str | None = None
    executed_at: str | None = None

    @classmethod
    def from_model(cls, execution: ServiceHookExecution) -> ServiceHookExecutionRead:
        return cls(
            execution_id=execution.execution_id,
            hook_id=execution.hook_id,
            organization_id=execution.organization_id,
            event_name=execution.event_name,
            status=execution.status,
            response_status_code=execution.response_status_code,
            error_message=execution.error_message,
            retry_count=execution.retry_count,
            duration_ms=execution.duration_ms,
            created_at=execution.created_at.isoformat()
            if execution.created_at
            else None,
            executed_at=execution.executed_at.isoformat()
            if execution.executed_at
            else None,
        )


class ServiceHookExecutionDetailRead(ServiceHookExecutionRead):
    """Detailed execution response including payload and response body."""

    event_payload: dict = Field(default_factory=dict)
    response_body: str | None = None

    @classmethod
    def from_model(
        cls, execution: ServiceHookExecution
    ) -> ServiceHookExecutionDetailRead:
        base = ServiceHookExecutionRead.from_model(execution)
        return cls(
            **base.model_dump(),
            event_payload=execution.event_payload or {},
            response_body=execution.response_body,
        )


def _resolve_actor_user_id(auth: dict) -> UUID | None:
    actor = auth.get("person_id")
    if not actor:
        return None
    try:
        return coerce_uuid(actor)
    except (TypeError, ValueError):
        return None


def _merge_policy_config(
    *,
    existing: dict | None,
    handler_type: HookHandlerType,
    circuit_breaker_failures: int | None,
    webhook_timeout_seconds: int | None,
) -> dict:
    config = dict(existing or {})
    if circuit_breaker_failures is not None:
        if circuit_breaker_failures <= 0:
            config.pop("circuit_breaker_failures", None)
        else:
            config["circuit_breaker_failures"] = circuit_breaker_failures

    if webhook_timeout_seconds is not None:
        if handler_type != HookHandlerType.WEBHOOK:
            raise HTTPException(
                status_code=400,
                detail="webhook_timeout_seconds is only valid for WEBHOOK hooks.",
            )
        config["timeout_seconds"] = webhook_timeout_seconds
    return config


def _get_mutable_hook_or_404(db: Session, org_id: UUID, hook_id: UUID) -> ServiceHook:
    hook = db.get(ServiceHook, hook_id)
    if hook is None or hook.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Hook not found")
    return hook


def _get_visible_hook_or_404(db: Session, org_id: UUID, hook_id: UUID) -> ServiceHook:
    hook = db.get(ServiceHook, hook_id)
    if hook is None or hook.organization_id not in {None, org_id}:
        raise HTTPException(status_code=404, detail="Hook not found")
    return hook


@router.get("", response_model=ListResponse[ServiceHookRead])
def list_service_hooks(
    q: str | None = Query(default=None, min_length=1, max_length=200),
    event_name: str | None = Query(default=None),
    handler_type: HookHandlerType | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """List service hooks for the current tenant (includes global hooks)."""
    items = ServiceHookService(db).list_for_org(
        org_id,
        name_contains=q,
        event_name=event_name,
        handler_type=handler_type,
        is_active=is_active,
    )
    page = items[offset : offset + limit]
    return ListResponse[ServiceHookRead](
        items=[ServiceHookRead.from_model(item) for item in page],
        count=len(items),
        offset=offset,
        limit=limit,
    )


@router.get("/{hook_id}", response_model=ServiceHookRead)
def get_service_hook(
    hook_id: UUID,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a service hook by ID if visible to current tenant."""
    hook = _get_visible_hook_or_404(db, org_id, hook_id)
    return ServiceHookRead.from_model(hook)


@router.get("/{hook_id}/stats", response_model=ServiceHookStatsRead)
def get_service_hook_stats(
    hook_id: UUID,
    days: int = Query(default=30, ge=1, le=365),
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get execution stats for a visible service hook."""
    hook = _get_visible_hook_or_404(db, org_id, hook_id)
    stats = ServiceHookService(db).execution_stats(hook.hook_id, days=days)
    return ServiceHookStatsRead(hook_id=hook.hook_id, days=days, stats=stats)


@router.get(
    "/{hook_id}/executions", response_model=ListResponse[ServiceHookExecutionRead]
)
def list_service_hook_executions(
    hook_id: UUID,
    status: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """List recent executions for a visible service hook."""
    hook = _get_visible_hook_or_404(db, org_id, hook_id)
    status_enum = None
    if status:
        try:
            status_enum = ExecutionStatus(status.upper())
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="Invalid execution status."
            ) from exc

    service = ServiceHookService(db)
    items = service.list_executions(
        hook.hook_id,
        status=status_enum,
        limit=limit,
        offset=offset,
    )
    total = service.count_executions(
        hook.hook_id,
        status=status_enum,
    )
    return ListResponse[ServiceHookExecutionRead](
        items=[ServiceHookExecutionRead.from_model(item) for item in items],
        count=total,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/{hook_id}/executions/{execution_id}",
    response_model=ServiceHookExecutionDetailRead,
)
def get_service_hook_execution(
    hook_id: UUID,
    execution_id: UUID,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get detailed execution payload/result for a visible hook execution."""
    _get_visible_hook_or_404(db, org_id, hook_id)
    service = ServiceHookService(db)
    try:
        execution = service.get_execution(
            hook_id,
            execution_id,
            organization_id=org_id,
        )
        return ServiceHookExecutionDetailRead.from_model(execution)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/{hook_id}/executions/{execution_id}/retry",
    response_model=ServiceHookExecutionRead,
)
def retry_service_hook_execution(
    hook_id: UUID,
    execution_id: UUID,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Retry a FAILED/DEAD execution for a tenant-visible hook."""
    _get_visible_hook_or_404(db, org_id, hook_id)
    service = ServiceHookService(db)
    try:
        execution = service.retry_execution(
            hook_id,
            execution_id,
            organization_id=org_id,
        )
        return ServiceHookExecutionRead.from_model(execution)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "",
    response_model=ServiceHookRead,
    status_code=status.HTTP_201_CREATED,
)
def create_service_hook(
    payload: ServiceHookCreate,
    auth: dict = Depends(require_tenant_auth),
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create an organization-scoped service hook."""
    handler_config = _merge_policy_config(
        existing=payload.handler_config,
        handler_type=payload.handler_type,
        circuit_breaker_failures=payload.circuit_breaker_failures,
        webhook_timeout_seconds=payload.webhook_timeout_seconds,
    )
    hook = ServiceHookService(db).create(
        organization_id=org_id,
        event_name=payload.event_name,
        handler_type=payload.handler_type,
        execution_mode=payload.execution_mode,
        handler_config=handler_config,
        conditions=payload.conditions,
        name=payload.name,
        description=payload.description,
        is_active=payload.is_active,
        priority=payload.priority,
        max_retries=payload.max_retries,
        retry_backoff_seconds=payload.retry_backoff_seconds,
        created_by_user_id=_resolve_actor_user_id(auth),
    )
    return ServiceHookRead.from_model(hook)


@router.patch("/{hook_id}", response_model=ServiceHookRead)
def update_service_hook(
    hook_id: UUID,
    payload: ServiceHookUpdate,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update an organization-scoped service hook."""
    current_hook = _get_mutable_hook_or_404(db, org_id, hook_id)
    updates = payload.model_dump(exclude_unset=True)
    cb = updates.pop("circuit_breaker_failures", None)
    timeout = updates.pop("webhook_timeout_seconds", None)
    if cb is not None or timeout is not None:
        handler_type = updates.get("handler_type") or current_hook.handler_type
        base_config = updates.get("handler_config") or current_hook.handler_config
        updates["handler_config"] = _merge_policy_config(
            existing=base_config,
            handler_type=handler_type,
            circuit_breaker_failures=cb,
            webhook_timeout_seconds=timeout,
        )
    hook = ServiceHookService(db).update(hook_id, **updates)
    return ServiceHookRead.from_model(hook)


@router.post("/{hook_id}/toggle", response_model=ServiceHookRead)
def toggle_service_hook(
    hook_id: UUID,
    payload: ServiceHookToggle,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Enable or disable an organization-scoped service hook."""
    _get_mutable_hook_or_404(db, org_id, hook_id)
    hook = ServiceHookService(db).toggle(hook_id, payload.enabled)
    return ServiceHookRead.from_model(hook)


@router.post("/actions/bulk/toggle", response_model=ServiceHookBulkResult)
def bulk_toggle_service_hooks(
    payload: ServiceHookBulkToggle,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Toggle many organization-scoped hooks."""
    result = ServiceHookService(db).bulk_toggle(
        payload.hook_ids,
        organization_id=org_id,
        is_active=payload.enabled,
    )
    return ServiceHookBulkResult(
        requested=int(result["requested"]),
        processed=int(result["updated"]),
        not_found_ids=list(result["not_found_ids"]),
    )


@router.delete("/{hook_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service_hook(
    hook_id: UUID,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete an organization-scoped service hook."""
    _get_mutable_hook_or_404(db, org_id, hook_id)
    ServiceHookService(db).delete(hook_id)
    return None


@router.post("/actions/bulk/delete", response_model=ServiceHookBulkResult)
def bulk_delete_service_hooks(
    payload: ServiceHookBulkDelete,
    org_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete many organization-scoped hooks."""
    result = ServiceHookService(db).bulk_delete(
        payload.hook_ids,
        organization_id=org_id,
    )
    return ServiceHookBulkResult(
        requested=int(result["requested"]),
        processed=int(result["deleted"]),
        not_found_ids=list(result["not_found_ids"]),
    )
