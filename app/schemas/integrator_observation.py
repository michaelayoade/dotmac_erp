"""Integrator ProductPort contract for Sub's invoice-accounting-sync observations.

``organization_id`` is deliberately absent: it is derived exclusively from the
authenticated service principal (``app/api/service_principal.py``), never from
the request body — there is no field here for it to disagree with.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.dotmac_sub.invoice_sync_outcomes import (
    CONTRACT_VERSION,
    SUPPORTED_DIGEST_VERSION,
    InvoiceSyncDisposition,
    InvoiceSyncIssueCode,
    InvoiceSyncSourceKind,
)
from dotmac_kernel.idempotency import MAX_KEY_LENGTH

_PROJECTION_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")


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
    digest_version: int = Field(strict=True)
    issues: tuple[IntegratorInvoiceSyncIssuePayload, ...] = ()
    observed_at: datetime | None = None
    contract_version: str = CONTRACT_VERSION
    idempotency_key: str = Field(..., min_length=1, max_length=MAX_KEY_LENGTH)

    @model_validator(mode="after")
    def _validate_digest(self) -> IntegratorInvoiceSyncObservationRequest:
        if not _PROJECTION_DIGEST_PATTERN.fullmatch(self.projection_fingerprint):
            raise ValueError(
                "projection_fingerprint must be exactly 64 lowercase hex "
                f"characters, got {self.projection_fingerprint!r}"
            )
        if self.digest_version != SUPPORTED_DIGEST_VERSION:
            raise ValueError(
                f"unsupported digest_version {self.digest_version!r}; this "
                f"ERP build only accepts {SUPPORTED_DIGEST_VERSION!r}"
            )
        return self


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
