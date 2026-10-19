#!/usr/bin/env python
"""Read-only numbering continuity report for INVOICE and CREDIT_NOTE.

Before these two series cut over to ``dotmac-numbering``, the module's
first allocation in the current period seeds from the legacy counter.
That is only sound if the legacy counter has not drifted below the
values already issued on real documents. This report names, per
organization and series:

* ``drift`` -- the highest value parsed from issued document numbers in
  the legacy row's current period exceeds ``current_number``; seeding
  from the row would reissue a value.
* ``unparseable`` -- issued numbers that carry the series prefix but no
  trailing integer after the final separator; their values cannot be
  accounted for by this report and need eyes before cutover.
* ``duplicates`` -- the same value issued twice in one period under
  different renderings (for example after a min-digits change); the
  module's receipt is unique on value within a period and refuses these.

Credit notes are ``ar.invoice`` rows with ``invoice_type=CREDIT_NOTE``
whose numbers were issued by the CREDIT_NOTE series; every other invoice
type is attributed to the INVOICE series.

Usage:
    python scripts/numbering_continuity_report.py --database-url postgresql://...
    DATABASE_URL=postgresql://... python scripts/numbering_continuity_report.py

READ-ONLY: SELECTs only. Exit code 1 when any finding is reported.
Deliberately does NOT import ``app.main``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.session_context import allow_cross_org
from app.models.finance.ar.invoice import Invoice, InvoiceType
from app.models.finance.core_config.numbering_sequence import (
    NumberingSequence,
    ResetFrequency,
    SequenceType,
)

SERIES = (SequenceType.INVOICE, SequenceType.CREDIT_NOTE)


@dataclass(frozen=True)
class Finding:
    kind: str
    organization_id: UUID
    series: str
    detail: str


def _period_key(sequence: NumberingSequence, year: int, month: int) -> str:
    if sequence.reset_frequency is ResetFrequency.MONTHLY:
        return f"{year:04d}-{month:02d}"
    if sequence.reset_frequency is ResetFrequency.YEARLY:
        return f"{year:04d}"
    return "*"


def _current_period_key(sequence: NumberingSequence) -> str | None:
    if sequence.current_year is None:
        return None
    return _period_key(sequence, sequence.current_year, sequence.current_month or 1)


def _parse_value(number: str, separator: str) -> int | None:
    tail = number.rsplit(separator, 1)[-1] if separator else number
    return int(tail) if tail.isdigit() else None


def continuity_findings(db: Session) -> list[Finding]:
    findings: list[Finding] = []
    # Genuinely cross-tenant read: every organization's counters and
    # issued numbers are compared in one scan, so the ORM org listener
    # is bypassed explicitly for both queries (production runtime is
    # the postgres superuser today; under a future RLS-bound app role
    # this report must move to per-org sessions).
    with allow_cross_org(db):
        sequences = {
            (row.organization_id, row.sequence_type): row
            for row in db.scalars(
                select(NumberingSequence).where(
                    NumberingSequence.sequence_type.in_(SERIES)
                )
            )
        }
        # Column select, not entity load: the report needs four facts per
        # document and must not drag relationship graphs into a read-only scan.
        invoices = db.execute(
            select(
                Invoice.organization_id,
                Invoice.invoice_number,
                Invoice.invoice_type,
                Invoice.invoice_date,
            ).where(Invoice.invoice_number.is_not(None))
        ).all()

    # (org, series, period) -> {value: [rendered, ...]}, and max parsed value.
    value_renderings: dict[tuple[UUID, SequenceType, str], dict[int, list[str]]] = {}
    max_values: dict[tuple[UUID, SequenceType, str], int] = {}
    unparseable: dict[tuple[UUID, SequenceType], list[str]] = {}
    for invoice in invoices:
        series = (
            SequenceType.CREDIT_NOTE
            if invoice.invoice_type is InvoiceType.CREDIT_NOTE
            else SequenceType.INVOICE
        )
        sequence = sequences.get((invoice.organization_id, series))
        if sequence is None:
            continue
        number = str(invoice.invoice_number)
        if sequence.prefix and not number.startswith(sequence.prefix):
            continue  # externally numbered document; not issued by this series
        period = _period_key(
            sequence, invoice.invoice_date.year, invoice.invoice_date.month
        )
        key = (invoice.organization_id, series, period)
        value = _parse_value(number, sequence.separator)
        if value is not None:
            value_renderings.setdefault(key, {}).setdefault(value, []).append(number)
        if value is None:
            unparseable.setdefault((invoice.organization_id, series), []).append(number)
        elif value > max_values.get(key, 0):
            max_values[key] = value

    for (org_id, series), sequence in sorted(
        sequences.items(), key=lambda item: (str(item[0][0]), item[0][1].value)
    ):
        current_period = _current_period_key(sequence)
        if current_period is not None:
            issued_max = max_values.get((org_id, series, current_period), 0)
            if issued_max > int(sequence.current_number or 0):
                findings.append(
                    Finding(
                        kind="drift",
                        organization_id=org_id,
                        series=series.value,
                        detail=(
                            f"period {current_period}: issued max {issued_max} "
                            f"exceeds legacy current_number "
                            f"{sequence.current_number}"
                        ),
                    )
                )
        for number in unparseable.get((org_id, series), [])[:5]:
            findings.append(
                Finding(
                    kind="unparseable",
                    organization_id=org_id,
                    series=series.value,
                    detail=f"issued number {number!r} has no trailing value",
                )
            )

    for (org_id, series, period), by_value in sorted(value_renderings.items()):
        for value, renderings in sorted(by_value.items()):
            if len(renderings) > 1:
                findings.append(
                    Finding(
                        kind="duplicates",
                        organization_id=org_id,
                        series=series.value,
                        detail=(
                            f"period {period}: value {value} issued as "
                            f"{sorted(set(renderings))}"
                        ),
                    )
                )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    engine = create_engine(args.database_url)
    try:
        with sessionmaker(bind=engine)() as db:
            findings = continuity_findings(db)
    finally:
        engine.dispose()
    payload = [
        {
            "kind": finding.kind,
            "organization_id": str(finding.organization_id),
            "series": finding.series,
            "detail": finding.detail,
        }
        for finding in findings
    ]
    json.dump({"findings": payload, "count": len(payload)}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
