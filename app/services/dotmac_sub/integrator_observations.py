"""Thin orchestration for the Integrator ProductPort observation receiver.

Two callers, one business-rule path: both the write route and the mirror
route build the same ``RecordInvoiceSyncOutcome`` command and validate it
through ``invoice_sync_outcomes._validated`` (that module's own construction
check — there is no public wrapper, and that module is read-only for this
slice, so the private function is imported directly rather than duplicating
its rules here). The write path additionally persists, via
``dotmac_kernel.idempotency.execute_once`` (ADR-0001: the kernel is ERP's sole
new at-most-once owner) wrapping ``record_invoice_sync_outcome``. The mirror
path never calls ``execute_once`` and never touches the outcome table.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from dotmac_kernel.idempotency import execute_once

from app.schemas.integrator_observation import IntegratorInvoiceSyncObservationRequest
from app.services.dotmac_sub.invoice_sync_outcomes import (
    InvoiceSyncIssueEvidence,
    InvoiceSyncOutcomeError,
    InvoiceSyncOutcomeReceipt,
    RecordInvoiceSyncOutcome,
    _validated as _validate_invoice_sync_command,
    record_invoice_sync_outcome,
)

# Fixed, endpoint-agnostic — ADR-0001 / dotmac_kernel.idempotency require a
# scope that identifies the OPERATION, not the route path.
IDEMPOTENCY_SCOPE = "integration.observations.invoices_accounting_sync.v1"


class IntegratorObservationValidationError(ValueError):
    """The observation payload fails the invoice-accounting-sync business rules."""


def _build_command(
    payload: IntegratorInvoiceSyncObservationRequest, *, organization_id: UUID
) -> RecordInvoiceSyncOutcome:
    return RecordInvoiceSyncOutcome(
        organization_id=organization_id,
        source_invoice_id=payload.source_invoice_id,
        source_updated_at=payload.source_updated_at,
        source_kind=payload.source_kind,
        disposition=payload.disposition,
        projection_fingerprint=payload.projection_fingerprint,
        digest_version=payload.digest_version,
        issues=tuple(
            InvoiceSyncIssueEvidence(
                code=issue.code,
                source_line_id=issue.source_line_id,
                expected_amount=issue.expected_amount,
                actual_amount=issue.actual_amount,
            )
            for issue in payload.issues
        ),
        observed_at=payload.observed_at,
        contract_version=payload.contract_version,
    )


def _fingerprint_payload(payload: IntegratorInvoiceSyncObservationRequest) -> str:
    """A stable digest of the normalized payload, excluding the idempotency key.

    A replayed key carrying a genuinely different payload must 409, not
    silently replay stale data — this is what ``execute_once`` compares
    against the stored fingerprint.
    """
    normalized = payload.model_dump(mode="json", exclude={"idempotency_key"})
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_invoice_sync_observation(
    payload: IntegratorInvoiceSyncObservationRequest, *, organization_id: UUID
) -> None:
    """Validate-only: the mirror route's entire job. Zero persistence.

    Raises ``IntegratorObservationValidationError`` on any check
    ``record_invoice_sync_outcome`` construction would itself reject. Never
    calls ``execute_once`` and never reads or writes the outcome table — it
    does not attempt to predict replay/duplicate status.
    """
    command = _build_command(payload, organization_id=organization_id)
    try:
        _validate_invoice_sync_command(command)
    except InvoiceSyncOutcomeError as exc:
        raise IntegratorObservationValidationError(str(exc)) from exc


def record_invoice_sync_observation(
    db: Session,
    *,
    organization_id: UUID,
    payload: IntegratorInvoiceSyncObservationRequest,
    capability_binding_id: str,
) -> dict[str, Any]:
    """Record (or replay) one observation at most once per idempotency key.

    Returns the plain dict the write route serializes into its response
    schema — the domain receipt fields from ``InvoiceSyncOutcomeReceipt``,
    identical on a byte-for-byte replay because ``execute_once`` returns the
    stored result without re-running ``operation``.
    """
    command = _build_command(payload, organization_id=organization_id)

    def _operation(session: Session) -> dict[str, Any]:
        try:
            receipt: InvoiceSyncOutcomeReceipt = record_invoice_sync_outcome(
                session, command
            )
        except InvoiceSyncOutcomeError as exc:
            raise IntegratorObservationValidationError(str(exc)) from exc
        return {
            "outcome_id": str(receipt.outcome_id),
            "occurrence_count": receipt.occurrence_count,
            "replayed": receipt.replayed,
            "resolved_prior_count": receipt.resolved_prior_count,
        }

    outcome = execute_once(
        db,
        tenant_id=organization_id,
        scope=IDEMPOTENCY_SCOPE,
        key=payload.idempotency_key,
        operation=_operation,
        operation_name=IDEMPOTENCY_SCOPE,
        fingerprint=_fingerprint_payload(payload),
        # Pure provenance (packet correction 4) — zero authorization weight.
        correlation_id=capability_binding_id,
    )
    return dict(outcome.result)
