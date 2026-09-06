"""Contract tests for Self-Care's additive invoice accounting v2 feed."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.services.dotmac_sub.client import (
    DotmacSubClient,
    DotmacSubConfig,
    DotmacSubParseError,
    InvoiceAccountingSyncDisposition,
    InvoiceAccountingSyncIssueCode,
    InvoiceAccountingSyncSourceKind,
    TaxApplication,
)


def _payload() -> dict[str, object]:
    return {
        "contract_version": "invoice-accounting-sync.v2",
        "source_kind": "native",
        "source_invoice_id": "11111111-1111-1111-1111-111111111111",
        "source_splynx_invoice_id": None,
        "account_id": "22222222-2222-2222-2222-222222222222",
        "account": {
            "id": "22222222-2222-2222-2222-222222222222",
            "display_name": "Contract account",
            "updated_at": "2026-09-06T10:00:00Z",
        },
        "invoice_number": "INV-1",
        "status": "issued",
        "currency": "NGN",
        "subtotal_before_discount": "1000.00",
        "discount_type": None,
        "discount_value": None,
        "discount_amount": "0.00",
        "discounted_subtotal": "1000.00",
        "tax_total": "75.00",
        "total": "1075.00",
        "balance_due": "1075.00",
        "issued_at": "2026-09-05T10:00:00Z",
        "due_at": None,
        "paid_at": None,
        "memo": None,
        "is_proforma": False,
        "updated_at": "2026-09-06T10:00:00Z",
        "disposition": "ready",
        "issues": [],
        "lines": [
            {
                "id": "33333333-3333-3333-3333-333333333333",
                "description": "Internet service",
                "quantity": "1",
                "unit_price": "1000",
                "source_amount": "1000.00",
                "net_amount_before_discount": "1000.00",
                "tax_amount_before_discount": "75.00",
                "gross_amount_before_discount": "1075.00",
                "tax_rate_id": "44444444-4444-4444-4444-444444444444",
                "tax_rate_code": "VAT75",
                "tax_rate_percent": "7.5",
                "tax_rate_is_active": True,
                "tax_application": "exclusive",
            }
        ],
    }


def _client() -> DotmacSubClient:
    return DotmacSubClient(DotmacSubConfig(api_url="https://sub.test", api_token="t"))


def test_accounting_v2_parser_admits_typed_ready_projection() -> None:
    record = _client()._parse_invoice_accounting_sync_v2(_payload())

    assert record.contract_version == "invoice-accounting-sync.v2"
    assert record.source_kind is InvoiceAccountingSyncSourceKind.NATIVE
    assert record.disposition is InvoiceAccountingSyncDisposition.READY
    assert record.tax_total == Decimal("75.00")
    assert record.lines[0].tax_application is TaxApplication.EXCLUSIVE
    assert record.lines[0].tax_rate_code == "VAT75"


def test_accounting_v2_parser_preserves_blocking_issue_evidence() -> None:
    payload = _payload()
    payload["disposition"] = "blocked"
    payload["issues"] = [
        {
            "code": "taxed_header_without_line_tax",
            "line_id": None,
            "expected_amount": "0.00",
            "actual_amount": "75.00",
        }
    ]

    record = _client()._parse_invoice_accounting_sync_v2(payload)

    assert record.disposition is InvoiceAccountingSyncDisposition.BLOCKED
    assert (
        record.issues[0].code
        is InvoiceAccountingSyncIssueCode.TAXED_HEADER_WITHOUT_LINE_TAX
    )
    assert record.issues[0].actual_amount == Decimal("75.00")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("contract_version", "invoice-accounting-sync.v3", "contract_version"),
        ("source_kind", "unknown", "source_kind"),
        ("disposition", "retry", "disposition"),
    ],
)
def test_accounting_v2_parser_rejects_contract_vocabulary_drift(
    field: str, value: str, message: str
) -> None:
    payload = _payload()
    payload[field] = value

    with pytest.raises(DotmacSubParseError, match=message):
        _client()._parse_invoice_accounting_sync_v2(payload)


def test_accounting_v2_parser_rejects_unknown_issue_code() -> None:
    payload = _payload()
    payload["issues"] = [{"code": "guess_the_tax"}]

    with pytest.raises(DotmacSubParseError, match="issue code"):
        _client()._parse_invoice_accounting_sync_v2(payload)


def test_accounting_v2_feed_uses_additive_endpoint_and_filters() -> None:
    client = _client()
    client._request = MagicMock(return_value={"items": [deepcopy(_payload())]})

    records = list(
        client.get_invoice_accounting_sync_v2(
            invoice_id="11111111-1111-1111-1111-111111111111",
            status="issued",
            updated_since="2026-09-01T00:00:00+00:00",
        )
    )

    assert len(records) == 1
    assert client._request.call_args.args[:2] == (
        "GET",
        "/invoices/accounting-sync/v2",
    )
    params = client._request.call_args.kwargs["params"]
    assert params["invoice_id"] == "11111111-1111-1111-1111-111111111111"
    assert params["status"] == "issued"
    assert params["updated_since"] == "2026-09-01T00:00:00+00:00"


def test_accounting_v2_feed_collects_one_bad_row_and_continues() -> None:
    client = _client()
    bad = _payload()
    bad["contract_version"] = "wrong"
    client._request = MagicMock(return_value={"items": [bad, _payload()]})
    errors: list[DotmacSubParseError] = []

    records = list(client.get_invoice_accounting_sync_v2(on_parse_error=errors.append))

    assert len(errors) == 1
    assert len(records) == 1
