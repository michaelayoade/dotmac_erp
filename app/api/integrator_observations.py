"""Integrator ProductPort receiver for Sub's invoice-accounting-sync observations.

Two routes (frozen contract, slice 1c):

- ``POST /integration/observations/{capability_binding_id}`` — write. Requires
  the write scope only.
- ``POST /integration/observations/{capability_binding_id}/mirror`` —
  validate-only. Accepts either the write or the mirror scope (a write-capable
  credential can safely also exercise the read-only mirror).

``capability_binding_id`` is pure URL-path provenance — it carries zero
authorization weight and is passed through to ``execute_once`` only as an
operator-facing correlation id. Authorization is entirely the scope-checked
service principal.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from dotmac_kernel.idempotency import IdempotencyConflict

from app.api.service_principal import (
    get_db_with_service_org,
    require_any_service_scope,
    require_service_auth,
    require_service_scope,
)
from app.schemas.integrator_observation import (
    IntegratorInvoiceSyncMirrorResponse,
    IntegratorInvoiceSyncObservationRequest,
    IntegratorInvoiceSyncObservationResponse,
)
from app.services.dotmac_sub.integrator_observations import (
    IntegratorObservationValidationError,
    record_invoice_sync_observation,
    validate_invoice_sync_observation,
)

router = APIRouter(prefix="/integration/observations", tags=["integrator-observations"])

SCOPE_WRITE = "integration:observations:write"
SCOPE_MIRROR = "integration:observations:mirror"

require_write_scope = require_service_scope(SCOPE_WRITE)
require_write_or_mirror_scope = require_any_service_scope(SCOPE_WRITE, SCOPE_MIRROR)


@router.post(
    "/{capability_binding_id}",
    response_model=IntegratorInvoiceSyncObservationResponse,
    dependencies=[Depends(require_write_scope)],
)
def record_integrator_observation(
    capability_binding_id: str,
    payload: IntegratorInvoiceSyncObservationRequest,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> IntegratorInvoiceSyncObservationResponse:
    try:
        result = record_invoice_sync_observation(
            db,
            organization_id=UUID(str(auth["organization_id"])),
            payload=payload,
            capability_binding_id=capability_binding_id,
        )
    except IntegratorObservationValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return IntegratorInvoiceSyncObservationResponse(**result)


@router.post(
    "/{capability_binding_id}/mirror",
    response_model=IntegratorInvoiceSyncMirrorResponse,
    dependencies=[Depends(require_write_or_mirror_scope)],
)
def mirror_integrator_observation(
    capability_binding_id: str,
    payload: IntegratorInvoiceSyncObservationRequest,
    auth: dict = Depends(require_service_auth),
) -> IntegratorInvoiceSyncMirrorResponse:
    """Validate-only. Never calls ``execute_once``, never touches the outcome
    table — no ``db`` dependency is taken for the operation itself."""
    try:
        validate_invoice_sync_observation(
            payload, organization_id=UUID(str(auth["organization_id"]))
        )
    except IntegratorObservationValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return IntegratorInvoiceSyncMirrorResponse(validated=True)
