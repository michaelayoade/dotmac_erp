"""Thin orchestration for the Integrator ProductPort observation receiver.

Three callers, one business-rule path: the write route, the mirror route and
the descriptor route all reason about the SAME
``invoices.accounting_sync.observation.v1`` capability. The write and mirror
paths both build the same ``RecordInvoiceSyncOutcome`` command from the
generic ProductObservation v1 envelope's ``.observation`` and validate it through
``invoice_sync_outcomes._validated`` (that module's own construction check —
there is no public wrapper, and that module is otherwise frozen for this
slice, so the private function is imported directly rather than duplicating
its rules here). The write path additionally persists, via
``dotmac_kernel.idempotency.execute_once`` (ADR-0001: the kernel is ERP's sole
at-most-once owner), wrapping ``record_invoice_sync_outcome``. The mirror
path never calls ``execute_once``; it runs the SAME construction/validation
and then a genuine READ-ONLY comparison against the durable outcome table via
``invoice_sync_outcomes.find_existing_outcome(for_update=False)`` — the exact
same 4-column identity lookup the write path's own stability check uses, so
the two can never independently drift on what "the same key" means.

The descriptor route publishes ERP's own product-port declaration; unlike
Sub, ERP has no live `IntegrationCapabilityBinding` row behind this binding
id — the binding is a single fixed deployment config value (see
``app.config.Settings.integrator_invoice_sync_binding_id``), so its
v3 descriptor is pure config-plus-schema, with no database read.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.orm import Session

from dotmac_kernel.idempotency import execute_once

from app.config import settings
from app.schemas.integrator_observation import (
    INVOICE_ACCOUNTING_SYNC_CAPABILITY,
    IntegratorDestinationScope,
    IntegratorInvoiceSyncEnvelope,
    InvoiceAccountingSyncObservation,
    ProductPortDescriptorV3,
)
from app.services.dotmac_sub.invoice_sync_outcomes import (
    InvoiceSyncIssueEvidence,
    InvoiceSyncOutcomeError,
    InvoiceSyncOutcomeReceipt,
    InvoiceSyncRevisionConflict,
    RecordInvoiceSyncOutcome,
    _validated as _validate_invoice_sync_command,
    find_existing_outcome,
    record_invoice_sync_outcome,
)

# Fixed, endpoint-agnostic — ADR-0001 / dotmac_kernel.idempotency require a
# scope that identifies the OPERATION, not the route path.
IDEMPOTENCY_SCOPE = "integration.observations.invoices_accounting_sync.v1"

#: Descriptor identity constants — fixed facts about this capability, not
#: deployment config (decided; see the task packet's "Descriptor identity
#: constants" section).
DESCRIPTOR_SCHEMA_VERSION: Final[Literal["dotmac.io/product-port-descriptor/v3"]] = (
    "dotmac.io/product-port-descriptor/v3"
)
WIRE_SCHEMA_VERSION: Final[Literal["dotmac.io/product-observation/v1"]] = (
    "dotmac.io/product-observation/v1"
)
DESCRIPTOR_APPLICATION: Final[Literal["erp"]] = "erp"
DESCRIPTOR_OWNER_MODULE: Final[str] = "app.services.dotmac_sub.integrator_observations"
DESCRIPTOR_CAPABILITY_SUMMARY: Final[str] = (
    "Records Self-Care invoice-accounting-sync observations as durable "
    "canonical evidence; never posts to the general ledger."
)
DESCRIPTOR_CONTRACT_VERSION: Final[Literal[1]] = 1
_DESCRIPTOR_ACTIVATION_STATE: Final[
    Literal["configured_disabled", "enabled", "quarantined", "retired"]
] = "configured_disabled"

#: The five stability fields the write path's own duplicate-key check
#: compares, and the SAME fields the mirror comparison compares — one
#: vocabulary, shared, so the two paths cannot drift on what "agrees" means.
_STABILITY_FIELDS: Final[tuple[str, ...]] = (
    "contract_version",
    "source_kind",
    "disposition",
    "projection_fingerprint",
    "issue_count",
)


class IntegratorObservationValidationError(ValueError):
    """The observation payload fails the invoice-accounting-sync business rules."""


class InvoiceSyncIdentityCollision(IntegratorObservationValidationError):
    """The same Self-Care invoice revision produced a genuinely different
    outcome — escalates to a human (409), never dead-lettered as a 422."""


@dataclass(frozen=True, slots=True)
class InvoiceSyncMirrorFieldDisagreement:
    field: str
    integrator: str | None
    erp: str | None


@dataclass(frozen=True, slots=True)
class InvoiceSyncMirrorComparison:
    """The mirror route's entire job: an honest, read-only verdict."""

    verdict: Literal["match", "missing", "blocked"]
    agrees: bool
    identity: str
    counterpart_identity: str | None
    blocking_reasons: tuple[str, ...]
    disagreements: tuple[InvoiceSyncMirrorFieldDisagreement, ...]


def _build_command(
    envelope: IntegratorInvoiceSyncEnvelope, *, organization_id: UUID
) -> RecordInvoiceSyncOutcome:
    """Build ERP's internal command from the envelope's inner observation.

    The ONE explicit field rename happens here and nowhere else:
    ``projection_fingerprint=envelope.observation.projection_digest``. Every
    other field maps by name; ``contract_version`` here is the STRING feed
    contract version (``envelope.observation.contract_version``), never the
    envelope's own int transport ``contract_version``.
    """
    observation = envelope.observation
    return RecordInvoiceSyncOutcome(
        organization_id=organization_id,
        source_invoice_id=observation.source_invoice_id,
        source_updated_at=observation.source_updated_at,
        source_kind=observation.source_kind,
        disposition=observation.disposition,
        projection_fingerprint=observation.projection_digest,
        digest_version=observation.digest_version,
        issues=tuple(
            InvoiceSyncIssueEvidence(
                code=issue.code,
                source_line_id=issue.source_line_id,
                expected_amount=issue.expected_amount,
                actual_amount=issue.actual_amount,
            )
            for issue in observation.issues
        ),
        observed_at=None,
        contract_version=observation.contract_version,
    )


def _fingerprint_observation(envelope: IntegratorInvoiceSyncEnvelope) -> str:
    """Bind one idempotency key to the entire accepted wire observation.

    Domain replay across independently delivered receipts remains keyed by
    invoice identity/revision in ``record_invoice_sync_outcome``. A retry of
    the SAME receipt must not silently change provider identity or provenance.
    """
    normalized = envelope.model_dump(mode="json")
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_provenance(envelope: IntegratorInvoiceSyncEnvelope) -> None:
    """Admit only the configured Sub stream addressed to this ERP binding."""
    if (
        envelope.source.installation_id
        != UUID(settings.integrator_invoice_sync_installation_id)
        or envelope.source.connector_key != "sub_accounting"
        or envelope.scope.kind != settings.integrator_invoice_sync_scope_kind
        or envelope.scope.ref != settings.integrator_invoice_sync_scope_ref
    ):
        raise IntegratorObservationValidationError(
            "observation provenance does not match this binding"
        )


def validate_invoice_sync_observation(
    envelope: IntegratorInvoiceSyncEnvelope, *, organization_id: UUID
) -> None:
    """Validate-only: shared construction check for both write and mirror.

    Raises ``IntegratorObservationValidationError`` on any check
    ``invoice_sync_outcomes._validated`` (construction-time validation, not
    the existing-row stability check) would itself reject — unsupported
    contract/digest version, malformed fingerprint, a disposition/issues
    inconsistency, or duplicate issue evidence. It can NEVER raise the
    narrower ``InvoiceSyncIdentityCollision``: that exception only comes
    from ``record_invoice_sync_outcome``'s existing-row comparison, which
    this function never reaches (it does not read the outcome table at
    all). Never reads or writes the outcome table.
    """
    _validate_provenance(envelope)
    command = _build_command(envelope, organization_id=organization_id)
    try:
        _validate_invoice_sync_command(command)
    except InvoiceSyncOutcomeError as exc:
        raise IntegratorObservationValidationError(str(exc)) from exc


def record_invoice_sync_observation(
    db: Session,
    *,
    organization_id: UUID,
    envelope: IntegratorInvoiceSyncEnvelope,
    idempotency_key: str,
    correlation_id: str | None,
):
    """Record (or replay) one observation at most once per idempotency key.

    Returns the kernel's ``IdempotentOutcome`` — ``.result`` is the plain
    dict the write route serializes into its response schema (the domain
    receipt fields from ``InvoiceSyncOutcomeReceipt``, identical on a
    byte-for-byte replay because ``execute_once`` returns the stored result
    without re-running ``operation``); ``.replayed`` is the KERNEL-level flag
    the route must OR with the stored domain-level ``replayed`` value — see
    ``app/api/integrator_observations.py`` for why that distinction matters.
    """
    _validate_provenance(envelope)
    command = _build_command(envelope, organization_id=organization_id)

    def _operation(session: Session) -> dict[str, Any]:
        try:
            receipt: InvoiceSyncOutcomeReceipt = record_invoice_sync_outcome(
                session, command
            )
        except InvoiceSyncRevisionConflict as exc:
            raise InvoiceSyncIdentityCollision(str(exc)) from exc
        except InvoiceSyncOutcomeError as exc:
            raise IntegratorObservationValidationError(str(exc)) from exc
        return {
            "outcome_id": str(receipt.outcome_id),
            "occurrence_count": receipt.occurrence_count,
            "replayed": receipt.replayed,
            "resolved_prior_count": receipt.resolved_prior_count,
            "disposition": command.disposition.value,
        }

    return execute_once(
        db,
        tenant_id=organization_id,
        scope=IDEMPOTENCY_SCOPE,
        key=idempotency_key,
        operation=_operation,
        operation_name=IDEMPOTENCY_SCOPE,
        fingerprint=_fingerprint_observation(envelope),
        correlation_id=correlation_id,
    )


def format_validation_errors(errors: Sequence[Mapping[str, object]]) -> str:
    """Pydantic/FastAPI's list-shaped ``.errors()`` into one readable string.

    Shared by the write route's ``RequestValidationError`` handling and the
    mirror route's own ``pydantic.ValidationError`` handling below, so the
    two never drift on message shape.
    """
    if not errors:
        return "request body failed validation"
    parts = []
    for error in errors:
        raw_loc = error.get("loc", ())
        loc_parts = raw_loc if isinstance(raw_loc, (list, tuple)) else ()
        loc = ".".join(str(part) for part in loc_parts)
        parts.append(f"{loc}: {error.get('msg', 'invalid')}")
    return "; ".join(parts)


def _best_effort_identity(raw_envelope: Mapping[str, object]) -> str:
    """A readable identity string even when the body fails to parse at all.

    Best-effort only: used exclusively for a ``verdict="blocked"`` report's
    ``identity`` field when the raw body could not be validated into an
    ``IntegratorInvoiceSyncEnvelope`` at all, so operators reading the
    mirror evidence still have something to correlate against.
    """
    observation = raw_envelope.get("observation")
    if not isinstance(observation, Mapping):
        return "unknown"
    parts = tuple(
        str(observation.get(field, "?"))
        for field in ("source_invoice_id", "source_updated_at", "digest_version")
    )
    return ":".join(parts)


def compare_invoice_sync_observation(
    db: Session,
    *,
    organization_id: UUID,
    raw_envelope: Mapping[str, object],
) -> InvoiceSyncMirrorComparison:
    """The mirror route's entire job: validate, then compare, never write.

    Accepts the RAW request body — not the write route's strict,
    ``extra="forbid"``/``Literal``-pinned ``IntegratorInvoiceSyncEnvelope``
    — because several genuine domain-fact disagreements (an out-of-pattern
    ``projection_digest``, an unrecognized enum value, a ``capability_id``
    mismatch) are exactly the class of disagreement a mirror pass exists to
    surface as evidence, never as a client-visible transport failure. Both
    that STRUCTURAL validation (via ``IntegratorInvoiceSyncEnvelope.model_validate``)
    and the existing BUSINESS-RULE validation happen here, uniformly
    converted into ``verdict="blocked"`` rather than raised — the mirror
    route must never answer any validation failure with a non-200 status
    (it would be misread as a transport outage, destroying the parity
    evidence a mirror pass exists to collect).

    A same-revision content disagreement (what would be an
    ``InvoiceSyncIdentityCollision`` on the write path) surfaces via the
    ``disagreements`` branch below (a named field mismatch against an
    existing row), not via ``blocking_reasons`` — ``blocking_reasons`` is
    reserved for a validation failure that happens before any row lookup
    ever runs. No row found at the identity key is an honest ``"missing"``,
    never reported as agreement.
    """
    identity = _best_effort_identity(raw_envelope)
    try:
        envelope = IntegratorInvoiceSyncEnvelope.model_validate(raw_envelope)
    except ValidationError as exc:
        return InvoiceSyncMirrorComparison(
            verdict="blocked",
            agrees=False,
            identity=identity,
            counterpart_identity=None,
            blocking_reasons=(format_validation_errors(exc.errors()),),
            disagreements=(),
        )

    observation = envelope.observation
    identity = (
        f"{observation.source_invoice_id}:"
        f"{observation.source_updated_at.isoformat()}:"
        f"{observation.digest_version}"
    )
    command = _build_command(envelope, organization_id=organization_id)
    try:
        # ADDITIONAL to `validate_invoice_sync_observation`, not a
        # replacement of it — this is the same shared construction check the
        # write path runs, just reused here ahead of the read-only lookup
        # below.
        validate_invoice_sync_observation(envelope, organization_id=organization_id)
    except IntegratorObservationValidationError as exc:
        return InvoiceSyncMirrorComparison(
            verdict="blocked",
            agrees=False,
            identity=identity,
            counterpart_identity=None,
            blocking_reasons=(str(exc),),
            disagreements=(),
        )

    existing = find_existing_outcome(
        db,
        organization_id=organization_id,
        source_invoice_id=observation.source_invoice_id,
        source_updated_at=observation.source_updated_at,
        digest_version=observation.digest_version,
        for_update=False,
    )
    if existing is None:
        return InvoiceSyncMirrorComparison(
            verdict="missing",
            agrees=False,
            identity=identity,
            counterpart_identity=None,
            blocking_reasons=(),
            disagreements=(),
        )

    issue_count = len(command.issues)
    incoming = {
        "contract_version": command.contract_version,
        "source_kind": command.source_kind.value,
        "disposition": command.disposition.value,
        "projection_fingerprint": command.projection_fingerprint,
        "issue_count": str(issue_count),
    }
    stored = {
        "contract_version": existing.contract_version,
        "source_kind": existing.source_kind,
        "disposition": existing.disposition,
        "projection_fingerprint": existing.projection_fingerprint,
        "issue_count": str(existing.issue_count),
    }
    disagreements = tuple(
        InvoiceSyncMirrorFieldDisagreement(
            field=field, integrator=incoming[field], erp=stored[field]
        )
        for field in _STABILITY_FIELDS
        if incoming[field] != stored[field]
    )
    counterpart_identity = str(existing.outcome_id)
    if disagreements:
        return InvoiceSyncMirrorComparison(
            verdict="blocked",
            agrees=False,
            identity=identity,
            counterpart_identity=counterpart_identity,
            blocking_reasons=(),
            disagreements=disagreements,
        )
    return InvoiceSyncMirrorComparison(
        verdict="match",
        agrees=True,
        identity=identity,
        counterpart_identity=counterpart_identity,
        blocking_reasons=(),
        disagreements=(),
    )


class ProductPortDescriptorError(ValueError):
    """The requested binding is not this ERP deployment's declared port."""


def _descriptor_digest(document: Mapping[str, object]) -> str:
    """SHA-256 over every published descriptor field, canonically encoded.

    Reproduced verbatim from Sub's own
    ``dotmac_sub/app/services/integrations/product_port_descriptor.py``
    (``descriptor_digest``) — same algorithm, same encoding, so the digest is
    independently reproducible against that shipped precedent.
    """
    encoded = json.dumps(
        document, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def invoice_accounting_sync_product_port_descriptor(
    capability_binding_id: UUID,
) -> ProductPortDescriptorV3:
    """Publish ERP's fixed invoice-accounting-sync ProductPort destination.

    ERP has no live ``IntegrationCapabilityBinding`` row behind this id —
    unlike Sub, the binding is a single fixed config value. Raises
    ``ProductPortDescriptorError`` when ``capability_binding_id`` does not
    match the configured binding.
    """
    configured_binding_id = UUID(settings.integrator_invoice_sync_binding_id)
    if capability_binding_id != configured_binding_id:
        raise ProductPortDescriptorError(
            "invoice-accounting-sync product port binding not found"
        )

    delivery_path = f"/api/v1/integration/observations/{capability_binding_id}"
    mirror_path = f"{delivery_path}/mirror"
    destination_scope = IntegratorDestinationScope(
        kind=settings.integrator_invoice_sync_scope_kind,
        ref=settings.integrator_invoice_sync_scope_ref,
    )
    destination_scope_document = {
        "kind": destination_scope.kind,
        "ref": destination_scope.ref,
    }
    # Activation is explicitly not this task's call (Michael's ruling) —
    # always published disabled until a separate, explicit activation step.
    activation_state = _DESCRIPTOR_ACTIVATION_STATE
    observation_schema = InvoiceAccountingSyncObservation.model_json_schema()
    capability_contract: dict[str, object] = {
        "command_schema": None,
        "result_schema": None,
        "observation_schema": observation_schema,
        "deprecation": None,
        "schema_grace": None,
        "contract_digest": _descriptor_digest(
            {
                "capability_id": INVOICE_ACCOUNTING_SYNC_CAPABILITY,
                "command_schema": None,
                "result_schema": None,
                "observation_schema": observation_schema,
            }
        ),
    }
    source_revision = _descriptor_digest(
        {
            "application": DESCRIPTOR_APPLICATION,
            "binding_id": str(capability_binding_id),
            "capability_id": INVOICE_ACCOUNTING_SYNC_CAPABILITY,
            "capability_summary": DESCRIPTOR_CAPABILITY_SUMMARY,
            "contract_version": DESCRIPTOR_CONTRACT_VERSION,
            "destination_scope": destination_scope_document,
            "owner_module": DESCRIPTOR_OWNER_MODULE,
            "wire_schema_version": WIRE_SCHEMA_VERSION,
            "capability_contract": capability_contract,
        }
    )
    published: dict[str, object] = {
        "schema_version": DESCRIPTOR_SCHEMA_VERSION,
        "wire_schema_version": WIRE_SCHEMA_VERSION,
        "application": DESCRIPTOR_APPLICATION,
        "owner_module": DESCRIPTOR_OWNER_MODULE,
        "capability_id": INVOICE_ACCOUNTING_SYNC_CAPABILITY,
        "capability_summary": DESCRIPTOR_CAPABILITY_SUMMARY,
        "contract_version": DESCRIPTOR_CONTRACT_VERSION,
        "destination_binding_id": str(capability_binding_id),
        "delivery_path": delivery_path,
        "mirror_path": mirror_path,
        "destination_scope": destination_scope_document,
        "activation_state": activation_state,
        "source_revision": source_revision,
        "capability_contract": capability_contract,
    }
    return ProductPortDescriptorV3(
        schema_version=DESCRIPTOR_SCHEMA_VERSION,
        wire_schema_version=WIRE_SCHEMA_VERSION,
        application=DESCRIPTOR_APPLICATION,
        owner_module=DESCRIPTOR_OWNER_MODULE,
        capability_id=INVOICE_ACCOUNTING_SYNC_CAPABILITY,
        capability_summary=DESCRIPTOR_CAPABILITY_SUMMARY,
        contract_version=DESCRIPTOR_CONTRACT_VERSION,
        destination_binding_id=capability_binding_id,
        delivery_path=delivery_path,
        mirror_path=mirror_path,
        destination_scope=destination_scope,
        activation_state=activation_state,
        source_revision=source_revision,
        capability_contract=capability_contract,
        descriptor_digest=_descriptor_digest(published),
    )
