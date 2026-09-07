from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.dotmac_sub.client import InvoiceAccountingSyncDisposition
from app.services.dotmac_sub.invoice_mismatch_report import (
    log_invoice_mismatch_census,
)


def test_report_logs_each_blocked_invoice_issue_without_writes(caplog) -> None:
    caplog.set_level(
        logging.INFO,
        logger="app.services.dotmac_sub.invoice_mismatch_report",
    )
    ready = SimpleNamespace(disposition=InvoiceAccountingSyncDisposition.READY)
    blocked = SimpleNamespace(
        disposition=InvoiceAccountingSyncDisposition.BLOCKED,
        source_invoice_id="11111111-1111-1111-1111-111111111111",
        invoice_number="INV-9",
        source_kind=SimpleNamespace(value="native"),
        updated_at=datetime(2026, 9, 7, tzinfo=UTC),
        currency="NGN",
        issues=(
            SimpleNamespace(
                code=SimpleNamespace(value="header_tax_mismatch"),
                line_id=None,
                expected_amount=Decimal("7.50"),
                actual_amount=Decimal("0.00"),
            ),
        ),
    )
    client = MagicMock()
    client.get_invoice_accounting_sync_v2.return_value = iter((ready, blocked))

    result = log_invoice_mismatch_census(client)

    assert result.inspected == 2
    assert result.mismatched_invoices == 1
    assert result.mismatch_issues == 1
    assert result.truncated is False
    mismatch = next(
        record
        for record in caplog.records
        if getattr(record, "event", None)
        == "dotmac_sub_invoice_accounting_mismatch_report"
    )
    assert mismatch.source_invoice_id == blocked.source_invoice_id
    assert mismatch.error_code == "header_tax_mismatch"
    assert mismatch.expected_amount == "7.50"
    assert mismatch.actual_amount == "0.00"
