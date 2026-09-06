"""Shadow consumer for Self-Care's invoice accounting v2 projection.

This module observes and records decisions only.  It deliberately has no
dependency on the ERP invoice posting service.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.ar.dotmac_sub_invoice_sync_outcome import (
    DotmacSubInvoiceSyncOutcome,
)
from app.services.dotmac_sub.client import (
    DotmacSubClient,
    DotmacSubParseError,
    InvoiceAccountingSyncRecord,
)
from app.services.dotmac_sub.invoice_sync_outcomes import (
    InvoiceSyncDisposition,
    InvoiceSyncIssueCode,
    InvoiceSyncIssueEvidence,
    InvoiceSyncSourceKind,
    RecordInvoiceSyncOutcome,
    record_invoice_sync_outcome,
)


class InvoiceSyncShadowContractError(ValueError):
    """The v2 feed could not be consumed without losing cursor safety."""


@dataclass(frozen=True, slots=True)
class InvoiceSyncShadowResult:
    observed: int
    ready: int
    blocked: int
    not_applicable: int
    replayed: int
    resolved_prior: int
    truncated: bool


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def invoice_projection_fingerprint(record: InvoiceAccountingSyncRecord) -> str:
    """Hash every admitted source fact using a stable JSON representation."""
    payload = json.dumps(
        _json_value(asdict(record)),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _latest_position(
    db: Session, organization_id: UUID
) -> tuple[datetime, UUID] | None:
    row = db.execute(
        select(
            DotmacSubInvoiceSyncOutcome.source_updated_at,
            DotmacSubInvoiceSyncOutcome.source_invoice_id,
        )
        .where(DotmacSubInvoiceSyncOutcome.organization_id == organization_id)
        .order_by(
            DotmacSubInvoiceSyncOutcome.source_updated_at.desc(),
            DotmacSubInvoiceSyncOutcome.source_invoice_id.desc(),
        )
        .limit(1)
    ).first()
    return (row[0], row[1]) if row is not None else None


def _command(
    organization_id: UUID, record: InvoiceAccountingSyncRecord
) -> RecordInvoiceSyncOutcome:
    if record.updated_at is None or record.updated_at.tzinfo is None:
        raise InvoiceSyncShadowContractError(
            f"invoice {record.source_invoice_id} has no timezone-aware updated_at"
        )
    try:
        source_invoice_id = UUID(record.source_invoice_id)
        source_kind = InvoiceSyncSourceKind(record.source_kind.value)
        disposition = InvoiceSyncDisposition(record.disposition.value)
        issues = tuple(
            InvoiceSyncIssueEvidence(
                code=InvoiceSyncIssueCode(issue.code.value),
                source_line_id=UUID(issue.line_id) if issue.line_id else None,
                expected_amount=issue.expected_amount,
                actual_amount=issue.actual_amount,
            )
            for issue in record.issues
        )
    except (TypeError, ValueError) as exc:
        raise InvoiceSyncShadowContractError(
            f"invoice {record.source_invoice_id} contains invalid typed evidence"
        ) from exc
    return RecordInvoiceSyncOutcome(
        organization_id=organization_id,
        source_invoice_id=source_invoice_id,
        source_updated_at=record.updated_at,
        source_kind=source_kind,
        disposition=disposition,
        projection_fingerprint=invoice_projection_fingerprint(record),
        issues=issues,
    )


def observe_invoice_accounting_v2(
    db: Session,
    client: DotmacSubClient,
    organization_id: UUID,
    *,
    invoice_id: UUID | None = None,
    batch_size: int = 500,
) -> InvoiceSyncShadowResult:
    """Record a bounded v2 shadow batch without creating or posting invoices."""
    if not 1 <= batch_size <= 2000:
        raise ValueError("batch_size must be between 1 and 2000")

    cursor = None if invoice_id is not None else _latest_position(db, organization_id)
    parse_errors: list[DotmacSubParseError] = []
    counts = {"ready": 0, "blocked": 0, "not_applicable": 0}
    observed = replayed = resolved_prior = 0
    truncated = False
    previous_position = cursor

    records = client.get_invoice_accounting_sync_v2(
        invoice_id=str(invoice_id) if invoice_id else None,
        updated_since=cursor[0].isoformat() if cursor else None,
        on_parse_error=parse_errors.append,
    )
    for record in records:
        command = _command(organization_id, record)
        position = (command.source_updated_at, command.source_invoice_id)
        if previous_position is not None and position < previous_position:
            raise InvoiceSyncShadowContractError("v2 feed order regressed")
        previous_position = position
        if cursor is not None and position <= cursor:
            continue
        if observed == batch_size:
            truncated = True
            break
        receipt = record_invoice_sync_outcome(db, command)
        observed += 1
        counts[command.disposition.value] += 1
        replayed += int(receipt.replayed)
        resolved_prior += receipt.resolved_prior_count

    if parse_errors:
        raise InvoiceSyncShadowContractError(
            f"v2 feed rejected {len(parse_errors)} malformed record(s); transaction must roll back"
        )
    return InvoiceSyncShadowResult(
        observed=observed,
        ready=counts["ready"],
        blocked=counts["blocked"],
        not_applicable=counts["not_applicable"],
        replayed=replayed,
        resolved_prior=resolved_prior,
        truncated=truncated,
    )
