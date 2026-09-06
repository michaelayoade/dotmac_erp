"""Persistence owner for durable Self-Care invoice synchronization outcomes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.finance.ar.dotmac_sub_invoice_sync_outcome import (
    DotmacSubInvoiceSyncIssue,
    DotmacSubInvoiceSyncOutcome,
)

CONTRACT_VERSION = "invoice-accounting-sync.v2"


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

    normalized = tuple(
        sorted((_issue_fingerprint(issue), issue) for issue in command.issues)
    )
    fingerprints = [item[0] for item in normalized]
    if len(fingerprints) != len(set(fingerprints)):
        raise InvoiceSyncOutcomeError("duplicate issue evidence is not allowed")
    return command.observed_at or datetime.now(timezone.utc), normalized


def record_invoice_sync_outcome(
    db: Session, command: RecordInvoiceSyncOutcome
) -> InvoiceSyncOutcomeReceipt:
    """Record or replay one source revision without committing the caller's transaction."""

    observed_at, normalized = _validated(command)
    existing = db.scalar(
        select(DotmacSubInvoiceSyncOutcome)
        .where(
            DotmacSubInvoiceSyncOutcome.organization_id == command.organization_id,
            DotmacSubInvoiceSyncOutcome.source_invoice_id == command.source_invoice_id,
            DotmacSubInvoiceSyncOutcome.source_updated_at == command.source_updated_at,
        )
        .with_for_update()
    )
    if existing is not None:
        stable = (
            existing.contract_version == command.contract_version
            and existing.source_kind == command.source_kind.value
            and existing.disposition == command.disposition.value
            and existing.projection_fingerprint == command.projection_fingerprint
            and existing.issue_count == len(normalized)
        )
        if not stable:
            raise InvoiceSyncOutcomeError(
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
