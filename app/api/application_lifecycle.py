"""Transport adapter for ERP-owned managed application lifecycle operations."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.service_principal import (
    get_db_with_service_org,
    require_explicit_service_scope,
)
from app.schemas.application_lifecycle import (
    ApplicationLifecycleApplyRequest,
    ApplicationLifecyclePlanRequest,
    ApplicationLifecycleReferenceRequest,
    ApplicationLifecycleResult,
)
from app.services.application_lifecycle import ManagedApplicationLifecycle

router = APIRouter(
    prefix="/integrations/application-lifecycle",
    tags=["application-lifecycle"],
)
_require_lifecycle_scope = require_explicit_service_scope("erp:application-lifecycle")


def _organization_id(auth: dict[str, object]) -> UUID:
    return UUID(str(auth["organization_id"]))


@router.post("/plan", response_model=ApplicationLifecycleResult)
def plan_application_lifecycle(
    payload: ApplicationLifecyclePlanRequest,
    auth: dict[str, object] = Depends(_require_lifecycle_scope),
    db: Session = Depends(get_db_with_service_org),
) -> ApplicationLifecycleResult:
    return ManagedApplicationLifecycle(db).plan(
        payload,
        organization_id=_organization_id(auth),
    )


@router.post("/apply", response_model=ApplicationLifecycleResult)
def apply_application_lifecycle(
    payload: ApplicationLifecycleApplyRequest,
    auth: dict[str, object] = Depends(_require_lifecycle_scope),
    db: Session = Depends(get_db_with_service_org),
) -> ApplicationLifecycleResult:
    return ManagedApplicationLifecycle(db).apply(
        payload,
        organization_id=_organization_id(auth),
    )


@router.post("/observe", response_model=ApplicationLifecycleResult)
def observe_application_lifecycle(
    payload: ApplicationLifecycleReferenceRequest,
    auth: dict[str, object] = Depends(_require_lifecycle_scope),
    db: Session = Depends(get_db_with_service_org),
) -> ApplicationLifecycleResult:
    return ManagedApplicationLifecycle(db).observe(
        _organization_id(auth), payload.operation_ref
    )


@router.post("/cancel", response_model=ApplicationLifecycleResult)
def cancel_application_lifecycle(
    payload: ApplicationLifecycleReferenceRequest,
    auth: dict[str, object] = Depends(_require_lifecycle_scope),
    db: Session = Depends(get_db_with_service_org),
) -> ApplicationLifecycleResult:
    return ManagedApplicationLifecycle(db).cancel(
        _organization_id(auth), payload.operation_ref
    )


__all__ = ["router"]
