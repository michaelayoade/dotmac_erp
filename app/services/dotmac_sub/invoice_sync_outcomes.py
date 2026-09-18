"""Persistence owner for durable Self-Care invoice synchronization outcomes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Final
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.finance.ar.dotmac_sub_invoice_sync_outcome import (
    DotmacSubInvoiceSyncIssue,
    DotmacSubInvoiceSyncOutcome,
)

CONTRACT_VERSION = "invoice-accounting-sync.v2"

# Sub's invoice-accounting-sync.v2 feed publishes ``digest_version`` alongside
# its canonical ``projection_digest``. This is the ONE place both
# ``app.schemas.integrator_observation`` and
# ``app.services.dotmac_sub.invoice_sync_shadow`` import it from — this module
# is the persistence/contract owner, and both of those already import
# ``CONTRACT_VERSION`` from here, so this is an existing, acyclic import
# direction, not a new dependency.
SUPPORTED_DIGEST_VERSION: Final[int] = 1


class InvoiceSyncDisposition(str, Enum):
    READY = "ready"
    BLOCKED = "blocked"
    NOT_APPLICABLE = "not_applicable"


class InvoiceSyncSourceKind(str, Enum):
    NATIVE = "native"
    SPLYNX_LEGACY = "splynx_legacy"


class InvoiceSyncIssueCode(str, Enum):
    NO_ACTIVE_LINES = "no_active_lines"
    LINE_AMOUNT_MISMATCH = "line_amount_mismatch"
    MISSING_TAX_RATE_REFERENCE = "missing_tax_rate_reference"
    TAX_SNAPSHOT_MISSING = "tax_snapshot_missing"
    HEADER_SUBTOTAL_MISMATCH = "header_subtotal_mismatch"
    TAXED_HEADER_WITHOUT_LINE_TAX = "taxed_header_without_line_tax"
    HEADER_TAX_MISMATCH = "header_tax_mismatch"
    HEADER_TOTAL_MISMATCH = "header_total_mismatch"
    LEGACY_HEADER_TOTALS_MISSING = "legacy_header_totals_missing"
    DISCOUNT_ALLOCATION_UNDEFINED = "discount_allocation_undefined"


@dataclass(frozen=True, slots=True)
class InvoiceSyncIssueEvidence:
    code: InvoiceSyncIssueCode
    source_line_id: UUID | None = None
    expected_amount: Decimal | None = None
    actual_amount: Decimal | None = None


@dataclass(frozen=True, slots=True)
class RecordInvoiceSyncOutcome:
    organization_id: UUID
    source_invoice_id: UUID
    source_updated_at: datetime
    source_kind: InvoiceSyncSourceKind
    disposition: InvoiceSyncDisposition
    projection_fingerprint: str
    digest_version: int
    issues: tuple[InvoiceSyncIssueEvidence, ...] = ()
    observed_at: datetime | None = None
    contract_version: str = CONTRACT_VERSION


@dataclass(frozen=True, slots=True)
class InvoiceSyncOutcomeReceipt:
    outcome_id: UUID
    occurrence_count: int
    replayed: bool
    resolved_prior_count: int


class InvoiceSyncOutcomeError(ValueError):
    """The supplied projection contradicts the durable outcome contract."""


class InvoiceSyncRevisionConflict(InvoiceSyncOutcomeError):
    """The same Self-Care invoice revision produced a genuinely different
    outcome — a real producer disagreement, not a malformed request. Callers
    distinguish this from the parent class to escalate to a human (409)
    rather than treat it as a terminal, dead-letterable validation failure
    (422)."""


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def _issue_fingerprint(issue: InvoiceSyncIssueEvidence) -> str:
    payload = {
        "actual_amount": _decimal_text(issue.actual_amount),
        "code": issue.code.value,
        "expected_amount": _decimal_text(issue.expected_amount),
        "source_line_id": str(issue.source_line_id) if issue.source_line_id else None,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validated(
    command: RecordInvoiceSyncOutcome,
) -> tuple[datetime, tuple[tuple[str, InvoiceSyncIssueEvidence], ...]]:
    if command.source_updated_at.tzinfo is None:
        raise InvoiceSyncOutcomeError("source_updated_at must be timezone-aware")
    if command.observed_at is not None and command.observed_at.tzinfo is None:
        raise InvoiceSyncOutcomeError("observed_at must be timezone-aware")
    if command.contract_version != CONTRACT_VERSION:
        raise InvoiceSyncOutcomeError(
            f"unsupported invoice sync contract {command.contract_version!r}"
        )
    if command.digest_version != SUPPORTED_DIGEST_VERSION:
        raise InvoiceSyncOutcomeError(
            f"unsupported digest_version {command.digest_version!r}; this ERP "
            f"build only accepts {SUPPORTED_DIGEST_VERSION!r}"
        )
    fingerprint = command.projection_fingerprint
    if len(fingerprint) != 64 or any(
        char not in "0123456789abcdef" for char in fingerprint
    ):
        raise InvoiceSyncOutcomeError(
            "projection_fingerprint must be a lowercase SHA-256 hex digest"
        )
    if command.disposition is InvoiceSyncDisposition.BLOCKED and not command.issues:
        raise InvoiceSyncOutcomeError("blocked outcomes require issue evidence")
    if command.disposition is not InvoiceSyncDisposition.BLOCKED and command.issues:
        raise InvoiceSyncOutcomeError("only blocked outcomes may carry issue evidence")

    # Sorted by an explicit key of already-comparable primitives, never by
    # falling through tuple comparison into the `InvoiceSyncIssueEvidence`
    # instances themselves: that dataclass has no `__lt__` (not
    # `order=True`), so two entries sharing a fingerprint would raise
    # `TypeError` on comparison — reachable in practice if a `Decimal("NaN")`
    # amount (a bare `Decimal` field admits it) ever produced equal
    # fingerprints for two otherwise-distinct issues.
    normalized = tuple(
        sorted(
            ((_issue_fingerprint(issue), issue) for issue in command.issues),
            key=lambda item: (
                item[0],
                str(item[1].source_line_id),
                _decimal_text(item[1].expected_amount) or "",
                _decimal_text(item[1].actual_amount) or "",
                item[1].code.value,
            ),
        )
    )
    fingerprints = [item[0] for item in normalized]
    if len(fingerprints) != len(set(fingerprints)):
        raise InvoiceSyncOutcomeError("duplicate issue evidence is not allowed")
    return command.observed_at or datetime.now(timezone.utc), normalized


def find_existing_outcome(
    db: Session,
    *,
    organization_id: UUID,
    source_invoice_id: UUID,
    source_updated_at: datetime,
    digest_version: int,
    for_update: bool,
) -> DotmacSubInvoiceSyncOutcome | None:
    """The one 4-column identity lookup, shared by the write path's stability
    check and the mirror route's read-only comparison — so the two can never
    independently drift on what "the same key" means.

    ``for_update`` controls row locking: the write path locks (``True``,
    unchanged behavior); the mirror comparison never writes, so it always
    passes ``False``.
    """
    stmt = select(DotmacSubInvoiceSyncOutcome).where(
        DotmacSubInvoiceSyncOutcome.organization_id == organization_id,
        DotmacSubInvoiceSyncOutcome.source_invoice_id == source_invoice_id,
        DotmacSubInvoiceSyncOutcome.source_updated_at == source_updated_at,
        DotmacSubInvoiceSyncOutcome.digest_version == digest_version,
    )
    if for_update:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def record_invoice_sync_outcome(
    db: Session, command: RecordInvoiceSyncOutcome
) -> InvoiceSyncOutcomeReceipt:
    """Record or replay one source revision without committing the caller's transaction."""

    observed_at, normalized = _validated(command)
    existing = find_existing_outcome(
        db,
        organization_id=command.organization_id,
        source_invoice_id=command.source_invoice_id,
        source_updated_at=command.source_updated_at,
        digest_version=command.digest_version,
        for_update=True,
    )
    if existing is not None:
        stable = (
            existing.contract_version == command.contract_version
            and existing.source_kind == command.source_kind.value
            and existing.disposition == command.disposition.value
            and existing.projection_fingerprint == command.projection_fingerprint
            and existing.digest_version == command.digest_version
            and existing.issue_count == len(normalized)
        )
        if not stable:
            raise InvoiceSyncRevisionConflict(
                "the same Self-Care invoice revision produced a different outcome"
            )
        existing.occurrence_count += 1
        existing.last_seen_at = observed_at
        db.flush()
        return InvoiceSyncOutcomeReceipt(
            outcome_id=existing.outcome_id,
            occurrence_count=existing.occurrence_count,
            replayed=True,
            resolved_prior_count=0,
        )

    outcome = DotmacSubInvoiceSyncOutcome(
        organization_id=command.organization_id,
        source_invoice_id=command.source_invoice_id,
        source_updated_at=command.source_updated_at,
        contract_version=command.contract_version,
        source_kind=command.source_kind.value,
        disposition=command.disposition.value,
        projection_fingerprint=command.projection_fingerprint,
        digest_version=command.digest_version,
        issue_count=len(normalized),
        first_seen_at=observed_at,
        last_seen_at=observed_at,
    )
    db.add(outcome)
    db.flush()
    for issue_fingerprint, issue in normalized:
        db.add(
            DotmacSubInvoiceSyncIssue(
                outcome_id=outcome.outcome_id,
                organization_id=command.organization_id,
                issue_code=issue.code.value,
                source_line_id=issue.source_line_id,
                expected_amount=issue.expected_amount,
                actual_amount=issue.actual_amount,
                issue_fingerprint=issue_fingerprint,
            )
        )

    resolved_prior_count = 0
    if command.disposition is InvoiceSyncDisposition.READY:
        result = db.execute(
            update(DotmacSubInvoiceSyncOutcome)
            .where(
                DotmacSubInvoiceSyncOutcome.organization_id == command.organization_id,
                DotmacSubInvoiceSyncOutcome.source_invoice_id
                == command.source_invoice_id,
                DotmacSubInvoiceSyncOutcome.source_updated_at
                < command.source_updated_at,
                DotmacSubInvoiceSyncOutcome.disposition
                == InvoiceSyncDisposition.BLOCKED.value,
                DotmacSubInvoiceSyncOutcome.resolved_at.is_(None),
            )
            .values(resolved_at=observed_at)
        )
        resolved_prior_count = result.rowcount or 0
    db.flush()
    return InvoiceSyncOutcomeReceipt(
        outcome_id=outcome.outcome_id,
        occurrence_count=1,
        replayed=False,
        resolved_prior_count=resolved_prior_count,
    )
