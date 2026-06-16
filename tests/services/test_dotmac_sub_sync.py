"""Unit tests for the dotmac_sub integration (client, config, webhook).

Covers the pure-logic surface that does not require a database session:
- decimal/record parsing from API payloads,
- ListResponse pagination envelope handling,
- DotmacSubConfig (is_configured / auth_header / env fallback),
- inbound webhook HMAC-SHA256 verification,
- webhook payload entity-id extraction,
- invoice status mapping (dotmac_sub → ERP).

The DB-backed sync behaviours (reseller parent/child wiring and the wholesale
GL-suppression rule) are exercised by the integration suite; the rule itself is
a single predicate — ``post_unposted_payments`` posts a payment only when its
customer has ``parent_customer_id IS NULL``.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.services.dotmac_sub.client import (
    DotmacSubClient,
    DotmacSubConfig,
    _dec,
)


# ---------------------------------------------------------------------------
# Decimal coercion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("123.45", Decimal("123.45")),
        (10, Decimal("10")),
        (None, Decimal("0")),
        ("", Decimal("0")),
        ("not-a-number", Decimal("0")),
    ],
)
def test_dec_coercion(value: object, expected: Decimal) -> None:
    assert _dec(value) == expected


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_config_is_configured() -> None:
    assert not DotmacSubConfig(api_url="", api_token="").is_configured()
    assert not DotmacSubConfig(api_url="https://x", api_token="").is_configured()
    assert DotmacSubConfig(api_url="https://x", api_token="tok").is_configured()


def test_config_auth_header() -> None:
    assert DotmacSubConfig(api_url="x", api_token="abc").auth_header == "Bearer abc"


def test_config_for_org_falls_back_to_env_when_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """for_org returns the env config when the IntegrationConfig lookup raises."""
    env = DotmacSubConfig(api_url="https://env", api_token="env-tok")
    monkeypatch.setattr(DotmacSubConfig, "from_settings", classmethod(lambda cls: env))
    db = MagicMock()
    db.execute.side_effect = RuntimeError("no db")  # forces the except path
    cfg = DotmacSubConfig.for_org(db, "00000000-0000-0000-0000-000000000000")
    assert cfg is env


# ---------------------------------------------------------------------------
# Pagination envelope + record parsing
# ---------------------------------------------------------------------------


def _client_with_responses(responses: list[object]) -> DotmacSubClient:
    client = DotmacSubClient(DotmacSubConfig(api_url="https://x", api_token="t"))
    client._request = MagicMock(side_effect=responses)  # type: ignore[method-assign]
    return client


def test_paginate_listresponse_envelope_stops_on_short_page() -> None:
    client = _client_with_responses(
        [{"items": [{"id": "1"}, {"id": "2"}], "count": 2, "limit": 100, "offset": 0}]
    )
    items = list(client._paginate("/things", page_size=100))
    assert [i["id"] for i in items] == ["1", "2"]
    client._request.assert_called_once()


def test_paginate_handles_bare_list_and_empty() -> None:
    client = _client_with_responses(
        [
            [],
        ]
    )
    assert list(client._paginate("/things")) == []


def test_parse_invoice_maps_fields_and_inline_allocations() -> None:
    client = DotmacSubClient(DotmacSubConfig(api_url="x", api_token="t"))
    inv = client._parse_invoice(
        {
            "id": "inv-1",
            "account_id": "acc-1",
            "invoice_number": "INV-100",
            "status": "issued",
            "currency": "NGN",
            "subtotal": "100.00",
            "tax_total": "7.50",
            "total": "107.50",
            "balance_due": "107.50",
            "issued_at": "2026-02-01",
            "lines": [
                {
                    "id": "l1",
                    "description": "Service",
                    "quantity": "1",
                    "unit_price": "100.00",
                    "amount": "100.00",
                }
            ],
            "payment_allocations": [
                {"id": "a1", "payment_id": "p1", "invoice_id": "inv-1", "amount": "50"}
            ],
        }
    )
    assert inv.id == "inv-1"
    assert inv.total == Decimal("107.50")
    assert len(inv.lines) == 1
    assert inv.lines[0].amount == Decimal("100.00")
    assert len(inv.allocations) == 1
    assert inv.allocations[0].amount == Decimal("50")


def test_parse_payment_effective_account_prefers_billing_account() -> None:
    client = DotmacSubClient(DotmacSubConfig(api_url="x", api_token="t"))
    pay = client._parse_payment(
        {
            "id": "p1",
            "account_id": "acc-a",
            "billing_account_id": "acc-b",
            "amount": "200.00",
            "currency": "NGN",
            "status": "succeeded",
            "allocations": [],
        }
    )
    assert pay.effective_account_id == "acc-b"
    assert pay.amount == Decimal("200.00")


def test_subscriber_full_name_prefers_display_then_company_then_parts() -> None:
    client = DotmacSubClient(DotmacSubConfig(api_url="x", api_token="t"))
    assert (
        client._parse_subscriber({"id": "s", "display_name": "Acme"}).full_name
        == "Acme"
    )
    assert (
        client._parse_subscriber({"id": "s", "company_name": "Beta Ltd"}).full_name
        == "Beta Ltd"
    )
    assert (
        client._parse_subscriber(
            {"id": "s", "first_name": "Ada", "last_name": "Lovelace"}
        ).full_name
        == "Ada Lovelace"
    )


# ---------------------------------------------------------------------------
# Invoice status mapping (dotmac_sub → ERP)
# ---------------------------------------------------------------------------


def test_map_invoice_status() -> None:
    from app.models.finance.ar.invoice import InvoiceStatus
    from app.services.dotmac_sub.sync._base import BaseSyncMixin

    m = BaseSyncMixin._map_invoice_status
    fake = object()  # self is unused by the method
    assert m(fake, "paid", Decimal("0")) == InvoiceStatus.PAID
    assert m(fake, "issued", Decimal("0")) == InvoiceStatus.PAID  # zero balance → paid
    assert m(fake, "issued", Decimal("10")) == InvoiceStatus.POSTED
    assert m(fake, "partially_paid", Decimal("5")) == InvoiceStatus.PARTIALLY_PAID
    assert m(fake, "void", Decimal("10")) == InvoiceStatus.VOID
    assert m(fake, "overdue", Decimal("10")) == InvoiceStatus.POSTED


# ---------------------------------------------------------------------------
# Webhook HMAC verification + entity-id extraction
# ---------------------------------------------------------------------------


def test_verify_webhook_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    import hashlib
    import hmac

    from app.api import dotmac_sub as api

    secret = "shhh"
    monkeypatch.setattr(
        api.settings, "dotmac_sub_webhook_secret", secret, raising=False
    )
    body = b'{"event_type":"invoice.paid","data":{"id":"inv-1"}}'
    good = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    assert api.verify_dotmac_sub_signature(body, good)
    assert api.verify_dotmac_sub_signature(body, f"sha256={good}")  # prefixed
    assert not api.verify_dotmac_sub_signature(body, "deadbeef")


def test_verify_webhook_signature_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import dotmac_sub as api

    monkeypatch.setattr(api.settings, "dotmac_sub_webhook_secret", None, raising=False)
    assert not api.verify_dotmac_sub_signature(b"x", "anything")


def test_webhook_entity_id_extraction() -> None:
    from app.services.dotmac_sub.webhook_dispatch import _entity_id

    assert _entity_id({"data": {"id": "abc"}}) == "abc"
    assert _entity_id({"id": "xyz"}) == "xyz"
    assert _entity_id({"data": {"entity_id": "e1"}}) == "e1"
    assert _entity_id({"event_type": "x"}) is None
