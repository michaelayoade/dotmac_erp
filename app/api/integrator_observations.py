"""Integrator ProductPort receiver for Sub's invoice-accounting-sync observations.

Three routes, the generic ProductObservation v1 wire (see
``app/schemas/integrator_observation.py`` for the full contract):

- ``POST /integration/observations/{capability_binding_id}`` — write.
- ``POST /integration/observations/{capability_binding_id}/mirror`` —
  read-only comparison, never writes.
- ``GET /integration/observations/{capability_binding_id}/descriptor`` —
  ERP's own published ProductPort declaration.

``capability_binding_id`` is checked against this deployment's ONE
configured binding (``settings.integrator_invoice_sync_binding_id``); a
mismatch is a plain-string 404 on all three routes — pure URL-path
provenance, carrying zero authorization weight. Authorization is entirely
the scope-checked service principal; ``organization_id`` comes exclusively
from ``auth["organization_id"]``, never from the envelope.

## Why this router owns its own error-handling scope

``app/errors.py`` already registers app-wide handlers for ``HTTPException``
and ``RequestValidationError`` that rewrite every response body into ERP's
own generic ``{"code", "message", "details"}`` shape — dropping the
top-level ``detail`` key the real Integrator client
(``dotmac_integrator.product_port.ObservationPortClient._refusal``) reads
directly. Modifying that shared file is out of scope (it is shared across
every other route in this application). Instead, ``_TypedErrorRoute`` below
gives ONLY these three routes their own exception-handling scope: it wraps
FastAPI's own request-handling call and answers a raw
``starlette.responses.JSONResponse`` before the app-level handlers ever see
the exception, bypassing them entirely for this router alone.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse, Response

from dotmac_kernel.idempotency import IdempotencyConflict, MAX_KEY_LENGTH

from app.api.service_principal import (
    get_db_with_service_org,
    require_any_service_scope,
    require_service_auth,
    require_service_scope,
)
from app.config import settings
from app.schemas.integrator_observation import (
    IntegratorInvoiceSyncEnvelope,
    IntegratorInvoiceSyncMirrorDisagreement,
    IntegratorInvoiceSyncMirrorReport,
    IntegratorInvoiceSyncReceipt,
    ProductPortDescriptorV3,
)
from app.services.dotmac_sub.integrator_observations import (
    IntegratorObservationValidationError,
    InvoiceSyncIdentityCollision,
    ProductPortDescriptorError,
    compare_invoice_sync_observation,
    format_validation_errors,
    invoice_accounting_sync_product_port_descriptor,
    record_invoice_sync_observation,
)

SCOPE_WRITE = "integration:observations:write"
SCOPE_MIRROR = "integration:observations:mirror"

require_write_scope = require_service_scope(SCOPE_WRITE)
require_write_or_mirror_scope = require_any_service_scope(SCOPE_WRITE, SCOPE_MIRROR)


def _validation_error_detail(exc: RequestValidationError) -> dict[str, object]:
    """FastAPI's own list-shaped ``exc.errors()`` into ERP's typed object."""
    return {
        "code": "invoices.accounting_sync.schema_rejected",
        "message": format_validation_errors(exc.errors()),
    }


class _TypedErrorRoute(APIRoute):
    """Preserve a typed/plain ``detail`` body for this router only.

    Catches ``RequestValidationError`` (raised during FastAPI's own request
    parsing, before the wrapped handler ever runs route code) and any
    ``HTTPException`` this router's own route functions raise, answering a
    raw JSON response with exactly ``{"detail": ...}`` — bypassing the
    app-wide handlers in ``app/errors.py`` entirely, for these routes only.
    Every other route in this application keeps its existing global error
    behavior unchanged.
    """

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Response:
            try:
                return await original_handler(request)
            except RequestValidationError as exc:
                return JSONResponse(
                    status_code=422,
                    content={"detail": _validation_error_detail(exc)},
                )
            except HTTPException as exc:
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": exc.detail},
                    headers=exc.headers,
                )

        return custom_route_handler


router = APIRouter(
    prefix="/integration/observations",
    tags=["integrator-observations"],
    route_class=_TypedErrorRoute,
)


def _bind(capability_binding_id: UUID) -> None:
    """Refuse a binding id that does not match this deployment's ONE
    configured binding — a plain-string 404, matching Sub's exact choice for
    a missing/wrong binding (maps to the client's retryable ``UNAVAILABLE``).
    """
    configured = UUID(settings.integrator_invoice_sync_binding_id)
    if capability_binding_id != configured:
        raise HTTPException(
            status_code=404,
            detail="Integrator invoice-accounting-sync binding not found",
        )


@router.post(
    "/{capability_binding_id}",
    response_model=IntegratorInvoiceSyncReceipt,
    dependencies=[Depends(require_write_scope)],
)
def record_integrator_invoice_sync_observation(
    capability_binding_id: UUID,
    envelope: IntegratorInvoiceSyncEnvelope,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=1, max_length=MAX_KEY_LENGTH
    ),
    x_correlation_id: str | None = Header(
        default=None, alias="X-Correlation-Id", max_length=200
    ),
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> IntegratorInvoiceSyncReceipt:
    _bind(capability_binding_id)
    organization_id = UUID(str(auth["organization_id"]))
    if not idempotency_key.strip():
        # `min_length=1` alone admits whitespace-only header values (a single
        # space passes it). `dotmac_kernel.idempotency`'s own `_validate`
        # would reject this with `BadRequestError` — a `dotmac_kernel`
        # exception type this app has no registered handler for, which would
        # otherwise fall through to a 500 and be misread by the real client
        # as a retryable UNAVAILABLE, retrying the same poison key forever.
        # Refuse it here as an honest, terminal 422 instead.
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invoices.accounting_sync.schema_rejected",
                "message": "Idempotency-Key must not be blank",
            },
        )
    try:
        outcome = record_invoice_sync_observation(
            db,
            organization_id=organization_id,
            envelope=envelope,
            idempotency_key=idempotency_key,
            correlation_id=x_correlation_id or str(capability_binding_id),
        )
    except InvoiceSyncIdentityCollision as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "invoices.accounting_sync.identity_collision",
                "message": str(exc),
            },
        ) from exc
    except IntegratorObservationValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invoices.accounting_sync.schema_rejected",
                "message": str(exc),
            },
        ) from exc
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    result = outcome.result
    # THE single most important correctness point in this route: a kernel-
    # level idempotency replay (the identical Idempotency-Key presented
    # again) must always answer replayed=true, even though the domain's own
    # FIRST-EVER stored write recorded replayed=False for itself. Collapsing
    # this into the domain-only value would make a retried delivery look
    # like a brand-new ACCEPTED to the real client, silently hiding a
    # double-send.
    replayed = outcome.replayed or bool(result["replayed"])
    return IntegratorInvoiceSyncReceipt(
        observation_id=str(result["outcome_id"]),
        outcome="replayed" if replayed else "recorded",
        processing_status=str(result["disposition"]),
        replayed=replayed,
        occurrence_count=int(result["occurrence_count"]),
        resolved_prior_count=int(result["resolved_prior_count"]),
    )


@router.post(
    "/{capability_binding_id}/mirror",
    response_model=IntegratorInvoiceSyncMirrorReport,
    dependencies=[Depends(require_write_or_mirror_scope)],
)
async def mirror_integrator_invoice_sync_observation(
    capability_binding_id: UUID,
    request: Request,
    auth: dict = Depends(require_service_auth),
    db: Session = Depends(get_db_with_service_org),
) -> IntegratorInvoiceSyncMirrorReport:
    """Read-only comparison. Never calls ``execute_once``, never writes a row.

    The real client sends the identical envelope and ``Idempotency-Key``
    header to this endpoint too; it is deliberately ignored here (no
    ``execute_once`` dependency is taken for the operation itself). ANY
    domain-validation failure answers HTTP 200 with ``verdict="blocked"`` —
    never a non-200 status, which the real client's ``mirror()`` would raise
    as a transport failure and misreport as an outage rather than a real
    disagreement.

    The body is read as a RAW JSON object here, deliberately bypassing the
    write route's strict, ``extra="forbid"``/``Literal``-pinned
    ``IntegratorInvoiceSyncEnvelope`` schema: several genuine domain-fact
    disagreements (an out-of-pattern ``projection_digest``, an unrecognized
    ``source_kind``/``disposition``/issue ``code``, a ``capability_id``
    mismatch) would otherwise be caught by FastAPI's own request parsing
    BEFORE this route body ever runs, producing a 422 the real client's
    ``ObservationPortClient.mirror()`` turns into a ``TransportFailure`` —
    exactly the failure mode a mirror pass exists to avoid, and precisely
    the class of disagreement it is FOR. All structural AND business-rule
    validation instead happens inside
    ``compare_invoice_sync_observation``, which converts every failure into
    an honest ``verdict="blocked"`` — always HTTP 200. The write route keeps
    its strict schema unchanged; this loosening is mirror-route-only.
    """
    _bind(capability_binding_id)
    organization_id = UUID(str(auth["organization_id"]))
    try:
        raw_body = await request.json()
    except ValueError:
        raw_body = {}
    if not isinstance(raw_body, dict):
        raw_body = {}
    comparison = compare_invoice_sync_observation(
        db, organization_id=organization_id, raw_envelope=raw_body
    )
    return IntegratorInvoiceSyncMirrorReport(
        verdict=comparison.verdict,
        agrees=comparison.agrees,
        identity=comparison.identity,
        counterpart_identity=comparison.counterpart_identity,
        blocking_reasons=comparison.blocking_reasons,
        disagreements=tuple(
            IntegratorInvoiceSyncMirrorDisagreement(
                field=item.field, integrator=item.integrator, erp=item.erp
            )
            for item in comparison.disagreements
        ),
    )


@router.get(
    "/{capability_binding_id}/descriptor",
    response_model=ProductPortDescriptorV3,
    dependencies=[Depends(require_write_or_mirror_scope)],
)
def read_invoice_sync_product_port_descriptor(
    capability_binding_id: UUID,
    _auth: dict = Depends(require_service_auth),
) -> ProductPortDescriptorV3:
    """Publish ERP-owned routing provenance without requiring activation."""
    _bind(capability_binding_id)
    try:
        return invoice_accounting_sync_product_port_descriptor(capability_binding_id)
    except ProductPortDescriptorError as exc:
        raise HTTPException(
            status_code=404,
            detail="Integrator invoice-accounting-sync binding not found",
        ) from exc
