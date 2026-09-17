"""Integrator ProductPort contract for Sub's invoice-accounting-sync observations.

``organization_id`` is deliberately absent: it is derived exclusively from the
authenticated service principal (``app/api/service_principal.py``), never from
the request body — there is no field here for it to disagree with.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.services.dotmac_sub.invoice_sync_outcomes import (
    CONTRACT_VERSION,
    InvoiceSyncDisposition,
    InvoiceSyncIssueCode,
    InvoiceSyncSourceKind,
)
from dotmac_kernel.idempotency import MAX_KEY_LENGTH


class IntegratorInvoiceSyncIssuePayload(BaseModel):
    """One piece of blocked-disposition evidence."""

    model_config = ConfigDict(extra="forbid")

    code: InvoiceSyncIssueCode
    source_line_id: UUID | None = None
    expected_amount: Decimal | None = None
    actual_amount: Decimal | None = None


class IntegratorInvoiceSyncObservationRequest(BaseModel):
    """The write/mirror request body (frozen contract, slice 1c)."""

    model_config = ConfigDict(extra="forbid")

    source_invoice_id: UUID
    source_updated_at: datetime
    source_kind: InvoiceSyncSourceKind
    disposition: InvoiceSyncDisposition
    projection_fingerprint: str
    issues: tuple[IntegratorInvoiceSyncIssuePayload, ...] = ()
    observed_at: datetime | None = None
    contract_version: str = CONTRACT_VERSION
    idempotency_key: str = Field(..., min_length=1, max_length=MAX_KEY_LENGTH)


class IntegratorInvoiceSyncObservationResponse(BaseModel):
    """The write route's success response — the domain receipt fields only.

    Kernel-level replay (``IdempotentOutcome.replayed`` — first attempt vs. a
    later attempt on the same key) is operationally invisible here by design;
    ``replayed`` below is the DOMAIN-level flag (whether this exact source
    revision was already recorded), matching
    ``InvoiceSyncOutcomeReceipt.replayed``.
    """

    model_config = ConfigDict(extra="forbid")

    outcome_id: UUID
    occurrence_count: int
    replayed: bool
    resolved_prior_count: int


class IntegratorInvoiceSyncMirrorResponse(BaseModel):
    """The mirror route's only success shape — validation only, no receipt."""

    model_config = ConfigDict(extra="forbid")

    validated: bool = True
