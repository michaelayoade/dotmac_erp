"""Log-only census for Self-Care invoice accounting contradictions."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.services.dotmac_sub.client import (
    DotmacSubClient,
    InvoiceAccountingSyncDisposition,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InvoiceMismatchReportResult:
    inspected: int
    mismatched_invoices: int
    mismatch_issues: int
    truncated: bool


def log_invoice_mismatch_census(
    client: DotmacSubClient,
    *,
    max_records: int | None = None,
) -> InvoiceMismatchReportResult:
    """Read v2 source projections and log each blocked invoice and issue.

    This reporter deliberately has no database session and performs no
    correction, outcome persistence, or ERP posting.
    """
    if max_records is not None and max_records < 1:
        raise ValueError("max_records must be positive when provided")

    inspected = mismatched_invoices = mismatch_issues = 0
    truncated = False
    for record in client.get_invoice_accounting_sync_v2():
        if max_records is not None and inspected == max_records:
            truncated = True
            break
        inspected += 1
        if record.disposition is not InvoiceAccountingSyncDisposition.BLOCKED:
            continue
        mismatched_invoices += 1
        for issue in record.issues:
            mismatch_issues += 1
            logger.warning(
                "dotmac_sub invoice accounting mismatch",
                extra={
                    "event": "dotmac_sub_invoice_accounting_mismatch_report",
                    "error_code": issue.code.value,
                    "source_invoice_id": record.source_invoice_id,
                    "source_invoice_number": record.invoice_number,
                    "source_kind": record.source_kind.value,
                    "source_updated_at": (
                        record.updated_at.isoformat()
                        if record.updated_at is not None
                        else None
                    ),
                    "currency": record.currency,
                    "source_line_id": issue.line_id,
                    "expected_amount": (
                        str(issue.expected_amount)
                        if issue.expected_amount is not None
                        else None
                    ),
                    "actual_amount": (
                        str(issue.actual_amount)
                        if issue.actual_amount is not None
                        else None
                    ),
                },
            )
    logger.info(
        "dotmac_sub invoice accounting mismatch census complete",
        extra={
            "event": "dotmac_sub_invoice_accounting_mismatch_report_complete",
            "inspected": inspected,
            "mismatched_invoices": mismatched_invoices,
            "mismatch_issues": mismatch_issues,
            "truncated": truncated,
        },
    )
    return InvoiceMismatchReportResult(
        inspected=inspected,
        mismatched_invoices=mismatched_invoices,
        mismatch_issues=mismatch_issues,
        truncated=truncated,
    )
