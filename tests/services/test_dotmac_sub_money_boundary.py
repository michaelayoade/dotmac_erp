"""E4 money-boundary behavior of the Sub-facing schemas.

Golden-style pins for the typed Money boundary on the dotmac_sub connector
records (invoice / credit-note / payment WHT evidence) and the Sub/CRM
payables command schema: exact NGN round-trips and byte-exact serialization
where the touched schemas carry money, plus every fail-closed rejection, and
proof that a rejected row fails ITS savepoint (raises) without any DB work.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.schemas.sync.dotmac_crm import CRMPurchaseInvoicePayload
from app.services.dotmac_sub.client import (
    AllocationRecord,
    CreditNoteRecord,
    DotmacSubClient,
    DotmacSubConfig,
    DotmacSubParseError,
    InvoiceLineRecord,
    InvoiceRecord,
    PaymentRecord,
)
from app.services.dotmac_sub.sync._credit_notes import CreditNoteSyncMixin
from app.services.dotmac_sub.sync._invoices import InvoiceSyncMixin
from app.services.dotmac_sub.sync._payments import PaymentSyncMixin
from app.services.dotmac_sub.sync._types import SyncResult
from app.services.finance.money_boundary import (
    MoneyBoundaryError,
    from_money,
    serialize_money,
)


def _invoice(**overrides: object) -> InvoiceRecord:
    defaults: dict = {
        "id": "inv-1",
        "account_id": "acct-1",
        "invoice_number": "SUB-INV-1",
        "status": "posted",
        "currency": "NGN",
        "subtotal": Decimal("45000.00"),
        "tax_total": Decimal("3375.00"),
        "total": Decimal("48375.00"),
        "balance_due": Decimal("48375.00"),
        "lines": [
            InvoiceLineRecord(
                id="l1",
                description="Service",
                quantity=Decimal("1"),
                unit_price=Decimal("45000.00"),
                amount=Decimal("45000.00"),
            )
        ],
    }
    defaults.update(overrides)
    return InvoiceRecord(**defaults)


def _payment(**overrides: object) -> PaymentRecord:
    defaults: dict = {
        "id": "pay-1",
        "account_id": "acct-1",
        "billing_account_id": "acct-1",
        "amount": Decimal("107.50"),
        "currency": "NGN",
        "status": "succeeded",
        "gross_amount": Decimal("107.50"),
        "net_amount": Decimal("100.00"),
        "wht_amount": Decimal("7.50"),
        "wht_rate": Decimal("7.5"),
    }
    defaults.update(overrides)
    return PaymentRecord(**defaults)


# ---------------------------------------------------------------------------
# Invoice / credit-note headers: typed Money, exact NGN round-trip
# ---------------------------------------------------------------------------


def test_invoice_boundary_money_round_trips_exactly() -> None:
    money = _invoice().boundary_money()
    assert from_money(money.subtotal) == (Decimal("45000.00"), "NGN")
    assert from_money(money.tax_total) == (Decimal("3375.00"), "NGN")
    assert from_money(money.total) == (Decimal("48375.00"), "NGN")
    assert money.balance_due is not None
    assert from_money(money.balance_due) == (Decimal("48375.00"), "NGN")
    # Byte-exact connector serialization for the header facts.
    assert serialize_money(money.total) == {"amount": "48375.00", "currency": "NGN"}


def test_invoice_boundary_money_rejects_missing_currency() -> None:
    with pytest.raises(MoneyBoundaryError, match="currency code is required"):
        _invoice(currency="").boundary_money()


def test_invoice_boundary_money_rejects_excess_precision() -> None:
    with pytest.raises(MoneyBoundaryError, match="subtotal.*minor-unit precision"):
        _invoice(subtotal=Decimal("45000.005")).boundary_money()


def test_invoice_boundary_money_validates_allocations() -> None:
    bad = _invoice(
        allocations=[
            AllocationRecord(
                id="a1",
                payment_id="p1",
                invoice_id="inv-1",
                amount=Decimal("10.005"),
            )
        ]
    )
    with pytest.raises(MoneyBoundaryError, match="allocation a1"):
        bad.boundary_money()


def test_credit_note_boundary_money_round_trips_exactly() -> None:
    cn = CreditNoteRecord(
        id="cn-1",
        account_id="acct-1",
        invoice_id="inv-1",
        credit_number="SUB-CN-1",
        status="applied",
        currency="NGN",
        subtotal=Decimal("1000.00"),
        tax_total=Decimal("75.00"),
        total=Decimal("1075.00"),
        applied_total=Decimal("1075.00"),
    )
    money = cn.boundary_money()
    assert from_money(money.total) == (Decimal("1075.00"), "NGN")
    assert money.applied_total is not None
    assert serialize_money(money.applied_total) == {
        "amount": "1075.00",
        "currency": "NGN",
    }


# ---------------------------------------------------------------------------
# Payment WHT evidence: typed Money, unchanged NGN facts
# ---------------------------------------------------------------------------


def test_payment_wht_evidence_round_trips_exactly() -> None:
    money = _payment().boundary_money()
    assert from_money(money.amount) == (Decimal("107.50"), "NGN")
    assert money.gross_amount is not None and money.net_amount is not None
    assert serialize_money(money.gross_amount) == {
        "amount": "107.50",
        "currency": "NGN",
    }
    assert serialize_money(money.net_amount) == {
        "amount": "100.00",
        "currency": "NGN",
    }
    assert serialize_money(money.wht_amount) == {"amount": "7.50", "currency": "NGN"}


def test_payment_boundary_money_rejects_excess_precision() -> None:
    with pytest.raises(MoneyBoundaryError, match="wht_amount.*minor-unit precision"):
        _payment(wht_amount=Decimal("7.505")).boundary_money()


def test_payment_boundary_money_optional_gross_net() -> None:
    money = _payment(gross_amount=None, net_amount=None).boundary_money()
    assert money.gross_amount is None
    assert money.net_amount is None


# ---------------------------------------------------------------------------
# The sync mixins fail the ROW (savepoint), before any DB/session work
# ---------------------------------------------------------------------------


class _InvoiceStub(InvoiceSyncMixin):
    def __init__(self) -> None:
        self._compute_hash = lambda payload: "hash"
        self._has_changed = lambda *args: True  # "changed" → proceed to admit


def test_invoice_sync_rejects_bad_money_before_any_db_work() -> None:
    result = SyncResult(success=True, entity_type="invoices")
    bad = _invoice(total=Decimal("48375.005"))
    with pytest.raises(MoneyBoundaryError, match="total.*minor-unit precision"):
        _InvoiceStub()._sync_single_invoice(bad, None, result, True)


class _PaymentStub(PaymentSyncMixin):
    pass


def test_payment_sync_rejects_bad_money_before_any_db_work() -> None:
    result = SyncResult(success=True, entity_type="payments")
    bad = _payment(net_amount=Decimal("100.005"))
    with pytest.raises(MoneyBoundaryError, match="net_amount.*minor-unit precision"):
        _PaymentStub()._sync_single_payment(bad, result, None, True)


# ---------------------------------------------------------------------------
# Admission runs on EVERY pass: an UNCHANGED record with invalid money facts
# fails its row — it cannot ride the unchanged-skip (or a status branch)
# around the boundary forever
# ---------------------------------------------------------------------------


class _UnchangedInvoiceStub(InvoiceSyncMixin):
    def __init__(self) -> None:
        self._compute_hash = lambda payload: "hash"
        self._has_changed = lambda *args: False  # "unchanged" → would skip


def test_unchanged_invoice_with_invalid_money_still_fails_admission() -> None:
    result = SyncResult(success=True, entity_type="invoices")
    bad = _invoice(total=Decimal("48375.005"))
    with pytest.raises(MoneyBoundaryError, match="total.*minor-unit precision"):
        _UnchangedInvoiceStub()._sync_single_invoice(bad, None, result, True)
    assert result.skipped == 0  # rejected, not silently skipped


def test_unchanged_invoice_with_fake_currency_still_fails_admission() -> None:
    result = SyncResult(success=True, entity_type="invoices")
    bad = _invoice(currency="ZZZ")
    with pytest.raises(MoneyBoundaryError, match="not provisioned"):
        _UnchangedInvoiceStub()._sync_single_invoice(bad, None, result, True)


class _UnchangedCreditNoteStub(CreditNoteSyncMixin):
    def __init__(self) -> None:
        self._compute_hash = lambda payload: "hash"
        self._has_changed = lambda *args: False


def test_unchanged_credit_note_with_invalid_money_still_fails_admission() -> None:
    result = SyncResult(success=True, entity_type="credit_notes")
    bad = CreditNoteRecord(
        id="cn-1",
        account_id="acct-1",
        invoice_id="inv-1",
        credit_number="SUB-CN-1",
        status="applied",
        currency="NGN",
        subtotal=Decimal("1000.005"),
        tax_total=Decimal("75.00"),
        total=Decimal("1075.00"),
    )
    with pytest.raises(MoneyBoundaryError, match="subtotal.*minor-unit precision"):
        _UnchangedCreditNoteStub()._sync_single_credit_note(bad, None, result, True)
    assert result.skipped == 0


def test_unsettled_payment_with_invalid_money_still_fails_admission() -> None:
    # A refunded/voided payment's amounts are still consumed (status hash,
    # reversal path) — admission runs before the settled-status branch.
    result = SyncResult(success=True, entity_type="payments")
    bad = _payment(status="refunded", wht_amount=Decimal("7.505"))
    with pytest.raises(MoneyBoundaryError, match="wht_amount.*minor-unit precision"):
        _PaymentStub()._sync_single_payment(bad, result, None, True)


# ---------------------------------------------------------------------------
# Sub/CRM payables command schema (vendor invoice)
# ---------------------------------------------------------------------------


def _payables_payload(**overrides: object) -> dict:
    payload: dict = {
        "crm_invoice_id": "src-inv-1",
        "crm_invoice_number": "VND-1",
        "crm_project_id": "proj-1",
        "installation_project_id": "inst-1",
        "erp_purchase_order_id": "PO-0001",
        "vendor_name": "Vendor Ltd",
        "currency": "NGN",
        "tax_rate_percent": Decimal("7.5"),
        "subtotal": Decimal("1000.00"),
        "tax_total": Decimal("75.00"),
        "total": Decimal("1075.00"),
        "items": [
            {
                "description": "Fibre splice",
                "quantity": Decimal("2"),
                "unit_price": Decimal("500.00"),
                "amount": Decimal("1000.00"),
            }
        ],
    }
    payload.update(overrides)
    return payload


def test_payables_payload_accepts_exact_money() -> None:
    payload = CRMPurchaseInvoicePayload(**_payables_payload())
    assert payload.total == Decimal("1075.00")


def test_payables_payload_rejects_excess_header_precision() -> None:
    with pytest.raises(ValidationError, match="minor-unit precision"):
        CRMPurchaseInvoicePayload(**_payables_payload(total=Decimal("1075.005")))


def test_payables_payload_rejects_excess_line_precision() -> None:
    bad_items = [
        {
            "description": "Fibre splice",
            "quantity": Decimal("2"),
            "unit_price": Decimal("500.00"),
            "amount": Decimal("1000.005"),
        }
    ]
    with pytest.raises(ValidationError, match="line 1 amount"):
        CRMPurchaseInvoicePayload(**_payables_payload(items=bad_items))


def test_payables_payload_rejects_invalid_currency() -> None:
    with pytest.raises(ValidationError, match="invalid ISO-4217"):
        CRMPurchaseInvoicePayload(**_payables_payload(currency="N1N"))


def test_payables_payload_unit_price_keeps_erp_decimal_scale() -> None:
    # unit_price is a rate, not boundary money — extra scale stays legal.
    items = [
        {
            "description": "Cable per metre",
            "quantity": Decimal("3"),
            "unit_price": Decimal("333.3333"),
            "amount": Decimal("1000.00"),
        }
    ]
    payload = CRMPurchaseInvoicePayload(**_payables_payload(items=items))
    assert payload.items[0].unit_price == Decimal("333.3333")


# ---------------------------------------------------------------------------
# Payables ingress: floats rejected BEFORE pydantic's Decimal coercion
# ---------------------------------------------------------------------------
# Policy (matches the connector's E4 outbound {"amount": "48375.00"} shape):
# money arrives as a string (canonical) or exact int/Decimal; float is
# rejected on the raw value in mode="before" validators — pydantic v2
# otherwise coerces float→Decimal before after-validators can see it.


def _payables_json(**overrides: object) -> str:
    payload: dict = {
        "crm_invoice_id": "src-inv-1",
        "crm_invoice_number": "VND-1",
        "crm_project_id": "proj-1",
        "installation_project_id": "inst-1",
        "erp_purchase_order_id": "PO-0001",
        "vendor_name": "Vendor Ltd",
        "currency": "NGN",
        "tax_rate_percent": "7.5",
        "subtotal": "1000.00",
        "tax_total": "75.00",
        "total": "1075.00",
        "items": [
            {
                "description": "Fibre splice",
                "quantity": "2",
                "unit_price": "500.00",
                "amount": "1000.00",
            }
        ],
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_payables_json_ingress_accepts_string_money_exactly() -> None:
    payload = CRMPurchaseInvoicePayload.model_validate_json(_payables_json())
    assert payload.total == Decimal("1075.00")
    assert payload.items[0].amount == Decimal("1000.00")


def test_payables_json_ingress_rejects_bare_json_float_header() -> None:
    # A bare fractional JSON number materializes as float — refused pre-coercion.
    raw = _payables_json().replace('"total": "1075.00"', '"total": 1075.005')
    with pytest.raises(ValidationError, match="money at ingress"):
        CRMPurchaseInvoicePayload.model_validate_json(raw)
    # Even an exactly-representable JSON float is refused — policy is by
    # type, not by luck of representability.
    raw = _payables_json().replace('"total": "1075.00"', '"total": 1075.5')
    with pytest.raises(ValidationError, match="money at ingress"):
        CRMPurchaseInvoicePayload.model_validate_json(raw)


def test_payables_json_ingress_rejects_bare_json_float_line_amount() -> None:
    raw = _payables_json().replace('"amount": "1000.00"', '"amount": 1000.25')
    with pytest.raises(ValidationError, match="money at ingress"):
        CRMPurchaseInvoicePayload.model_validate_json(raw)


def test_payables_json_ingress_rejects_integer_money_tokens() -> None:
    # Wire policy: external money is a canonical decimal STRING only —
    # EVERY JSON number token is rejected, integers included.
    raw = _payables_json().replace('"subtotal": "1000.00"', '"subtotal": 1000')
    with pytest.raises(ValidationError, match="money at ingress"):
        CRMPurchaseInvoicePayload.model_validate_json(raw)
    raw = _payables_json().replace('"amount": "1000.00"', '"amount": 1000')
    with pytest.raises(ValidationError, match="money at ingress"):
        CRMPurchaseInvoicePayload.model_validate_json(raw)


def test_payables_dict_ingress_rejects_python_float_and_int() -> None:
    with pytest.raises(ValidationError, match="money at ingress"):
        CRMPurchaseInvoicePayload.model_validate(_payables_payload(total=1075.00))
    with pytest.raises(ValidationError, match="money at ingress"):
        CRMPurchaseInvoicePayload.model_validate(_payables_payload(subtotal=1000))
    bad_items = [
        {
            "description": "Fibre splice",
            "quantity": Decimal("2"),
            "unit_price": Decimal("500.00"),
            "amount": 1000.25,
        }
    ]
    with pytest.raises(ValidationError, match="money at ingress"):
        CRMPurchaseInvoicePayload.model_validate(_payables_payload(items=bad_items))


def test_payables_ingress_rejects_non_finite_values() -> None:
    # NaN/Infinity as strings parse as Decimals — refused explicitly at the
    # ingress layer with the typed message, at header and line level.
    for bad in ("NaN", "Infinity", "-Infinity"):
        raw = _payables_json().replace('"total": "1075.00"', f'"total": "{bad}"')
        with pytest.raises(ValidationError, match="non-finite"):
            CRMPurchaseInvoicePayload.model_validate_json(raw)
        raw = _payables_json().replace('"amount": "1000.00"', f'"amount": "{bad}"')
        with pytest.raises(ValidationError, match="non-finite"):
            CRMPurchaseInvoicePayload.model_validate_json(raw)
    # As Python floats they are refused by type (floats never enter).
    with pytest.raises(ValidationError, match="money at ingress"):
        CRMPurchaseInvoicePayload.model_validate(_payables_payload(total=float("inf")))
    with pytest.raises(ValidationError, match="non-finite"):
        CRMPurchaseInvoicePayload.model_validate(
            _payables_payload(total=Decimal("NaN"))
        )


def test_payables_dict_ingress_still_accepts_internal_decimal() -> None:
    # Internal Python callers may pass Decimal (finite) — the strings-only
    # rule is a WIRE rule.
    payload = CRMPurchaseInvoicePayload.model_validate(_payables_payload())
    assert payload.total == Decimal("1075.00")


# ---------------------------------------------------------------------------
# Canaries through the REAL DotmacSubClient parsers (raw wire payloads)
# ---------------------------------------------------------------------------


def _client() -> DotmacSubClient:
    return DotmacSubClient(DotmacSubConfig(api_url="https://x", api_token="t"))


def _raw_invoice(**overrides: object) -> dict:
    payload: dict = {
        "id": "inv-raw-1",
        "account_id": "acct-1",
        "invoice_number": "SUB-INV-9",
        "status": "posted",
        "currency": "NGN",
        "subtotal": "45000.00",
        "tax_total": "3375.00",
        "total": "48375.00",
        "balance_due": "48375.00",
        "updated_at": "2026-07-01T10:00:00+00:00",
        "lines": [
            {
                "id": "l1",
                "description": "Service",
                "quantity": "1",
                "unit_price": "45000.00",
                "amount": "45000.00",
            }
        ],
    }
    payload.update(overrides)
    return payload


def _raw_payment(**overrides: object) -> dict:
    payload: dict = {
        "id": "pay-raw-1",
        "account_id": "acct-1",
        "billing_account_id": "acct-1",
        "amount": "107.50",
        "currency": "NGN",
        "status": "succeeded",
        "updated_at": "2026-07-01T11:00:00+00:00",
    }
    payload.update(overrides)
    return payload


def _raw_credit_note(**overrides: object) -> dict:
    payload: dict = {
        "id": "cn-raw-1",
        "account_id": "acct-1",
        "credit_number": "SUB-CN-9",
        "status": "applied",
        "currency": "NGN",
        "subtotal": "1000.00",
        "tax_total": "75.00",
        "total": "1075.00",
        "applied_total": "1075.00",
        "updated_at": "2026-07-01T12:00:00+00:00",
    }
    payload.update(overrides)
    return payload


def test_parse_invoice_happy_path_is_exact() -> None:
    inv = _client()._parse_invoice(_raw_invoice())
    assert inv.total == Decimal("48375.00")
    assert inv.currency == "NGN"
    assert inv.lines[0].amount == Decimal("45000.00")
    money = inv.boundary_money()
    assert serialize_money(money.total) == {"amount": "48375.00", "currency": "NGN"}


def test_parse_invoice_rejects_malformed_amount() -> None:
    with pytest.raises(DotmacSubParseError, match="malformed money fact") as exc_info:
        _client()._parse_invoice(_raw_invoice(total="not-money"))
    # The typed error carries row identity + updated_at so the sync loop can
    # fail the row and hold the watermark at it.
    assert exc_info.value.record == "Sub invoice inv-raw-1"
    assert exc_info.value.updated_at == "2026-07-01T10:00:00+00:00"


def test_parse_invoice_rejects_missing_money_fact_instead_of_zero() -> None:
    payload = _raw_invoice()
    del payload["total"]
    with pytest.raises(DotmacSubParseError, match="required money fact 'total'"):
        _client()._parse_invoice(payload)


def test_parse_invoice_rejects_missing_currency_no_functional_default() -> None:
    payload = _raw_invoice()
    del payload["currency"]
    with pytest.raises(DotmacSubParseError, match="currency is required"):
        _client()._parse_invoice(payload)
    with pytest.raises(DotmacSubParseError, match="currency is required"):
        _client()._parse_invoice(_raw_invoice(currency="   "))


def test_parse_invoice_rejects_float_typed_amount() -> None:
    with pytest.raises(DotmacSubParseError, match="refusing float"):
        _client()._parse_invoice(_raw_invoice(subtotal=45000.005))


def test_parse_invoice_rejects_malformed_allocation_amount() -> None:
    bad = _raw_invoice(
        payment_allocations=[
            {"id": "a1", "payment_id": "p1", "invoice_id": "inv-raw-1", "amount": ""}
        ]
    )
    with pytest.raises(DotmacSubParseError, match="allocation a1"):
        _client()._parse_invoice(bad)


def test_parse_invoice_excess_precision_fails_at_the_boundary_layer() -> None:
    # "48375.005" is a syntactically valid decimal — parse admits it, and the
    # E4 boundary (boundary_money) rejects it exactly, never rounds it.
    inv = _client()._parse_invoice(_raw_invoice(total="48375.005"))
    assert inv.total == Decimal("48375.005")
    with pytest.raises(MoneyBoundaryError, match="total.*minor-unit precision"):
        inv.boundary_money()


def test_parse_invoice_fake_currency_fails_at_the_boundary_layer() -> None:
    inv = _client()._parse_invoice(_raw_invoice(currency="ZZZ"))
    with pytest.raises(MoneyBoundaryError, match="not provisioned"):
        inv.boundary_money()


def test_parse_invoice_missing_line_amount_stays_none_for_derivation() -> None:
    payload = _raw_invoice()
    del payload["lines"][0]["amount"]
    inv = _client()._parse_invoice(payload)
    assert inv.lines[0].amount is None  # ERP derives qty x unit_price itself


def test_parse_payment_happy_path_is_exact() -> None:
    pay = _client()._parse_payment(
        _raw_payment(
            gross_amount="107.50",
            net_amount="100.00",
            wht_amount="7.50",
            wht_rate="7.5",
        )
    )
    assert pay.amount == Decimal("107.50")
    assert pay.refunded_amount == Decimal("0")  # documented absent default
    money = pay.boundary_money()
    assert from_money(money.amount) == (Decimal("107.50"), "NGN")


def test_parse_payment_rejects_missing_currency() -> None:
    payload = _raw_payment()
    del payload["currency"]
    with pytest.raises(DotmacSubParseError, match="currency is required"):
        _client()._parse_payment(payload)


def test_parse_payment_rejects_malformed_and_number_typed_amounts() -> None:
    with pytest.raises(DotmacSubParseError, match="malformed money fact"):
        _client()._parse_payment(_raw_payment(amount="12,50"))
    # Wire policy: strings only — float AND int number tokens are refused.
    with pytest.raises(DotmacSubParseError, match="refusing float"):
        _client()._parse_payment(_raw_payment(amount=107.5))
    with pytest.raises(DotmacSubParseError, match="refusing int"):
        _client()._parse_payment(_raw_payment(amount=107))
    with pytest.raises(DotmacSubParseError, match="refusing float"):
        _client()._parse_payment(_raw_payment(amount=float("inf")))
    # A documented-default field must still parse strictly when present.
    with pytest.raises(DotmacSubParseError, match="malformed money fact"):
        _client()._parse_payment(_raw_payment(refunded_amount="oops"))


def test_parse_rejects_non_finite_money_strings() -> None:
    for bad in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(DotmacSubParseError, match="non-finite"):
            _client()._parse_payment(_raw_payment(amount=bad))
        with pytest.raises(DotmacSubParseError, match="non-finite"):
            _client()._parse_invoice(_raw_invoice(total=bad))


def test_parse_invoice_rejects_integer_money_tokens() -> None:
    # Strings-only wire rule applies to the Sub feeds too.
    with pytest.raises(DotmacSubParseError, match="refusing int"):
        _client()._parse_invoice(_raw_invoice(subtotal=45000))
    with pytest.raises(DotmacSubParseError, match="refusing int"):
        _client()._parse_credit_note(_raw_credit_note(total=1075))


def test_parse_payment_rejects_missing_amount() -> None:
    payload = _raw_payment()
    del payload["amount"]
    with pytest.raises(DotmacSubParseError, match="required money fact 'amount'"):
        _client()._parse_payment(payload)


def test_parse_credit_note_happy_path_and_rejections() -> None:
    cn = _client()._parse_credit_note(_raw_credit_note())
    assert cn.total == Decimal("1075.00")
    assert from_money(cn.boundary_money().total) == (Decimal("1075.00"), "NGN")

    payload = _raw_credit_note()
    del payload["currency"]
    with pytest.raises(DotmacSubParseError, match="currency is required"):
        _client()._parse_credit_note(payload)
    with pytest.raises(DotmacSubParseError, match="malformed money fact"):
        _client()._parse_credit_note(_raw_credit_note(subtotal="NaN-ish"))
    with pytest.raises(DotmacSubParseError, match="refusing float"):
        _client()._parse_credit_note(_raw_credit_note(total=1075.5))


# ---------------------------------------------------------------------------
# A parse-rejected row fails THAT row: the feed continues, the run succeeds,
# and the watermark is held at the failed row for retry
# ---------------------------------------------------------------------------


def _feed_client(items: list[dict]) -> DotmacSubClient:
    client = _client()
    client._request = MagicMock(return_value={"items": items})  # type: ignore[method-assign]
    return client


def test_feed_generator_survives_a_bad_row_via_collector() -> None:
    client = _feed_client([_raw_invoice(total="oops"), _raw_invoice(id="inv-ok")])
    seen_errors: list[DotmacSubParseError] = []
    records = list(client.get_invoices(on_parse_error=seen_errors.append))
    assert [record.id for record in records] == ["inv-ok"]
    assert len(seen_errors) == 1
    assert seen_errors[0].record == "Sub invoice inv-raw-1"


def test_feed_generator_without_collector_raises_typed_error() -> None:
    client = _feed_client([_raw_invoice(total="oops")])
    with pytest.raises(DotmacSubParseError, match="malformed money fact"):
        list(client.get_invoices())


def _tolerant_parse_datetime(value: str | None) -> datetime | None:
    """Mimic the production ``_parse_datetime``: malformed/missing → None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class _InvoiceLoopHarness(InvoiceSyncMixin):
    def __init__(self, client: DotmacSubClient) -> None:
        self.client = client
        self.db = MagicMock()
        self._parse_datetime = _tolerant_parse_datetime
        self._get_sync_watermark = lambda entity_type: None
        self._advance_sync_watermark = MagicMock()
        self._sync_single_invoice = MagicMock()
        self._reprime_tenant_context = MagicMock()


def test_invoice_sync_fails_the_row_not_the_run_on_parse_rejection() -> None:
    bad_first = _raw_invoice(total="oops")  # rejected at parse
    good_second = _raw_invoice(id="inv-ok", updated_at="2026-07-02T10:00:00+00:00")
    harness = _InvoiceLoopHarness(_feed_client([bad_first, good_second]))

    result = harness.sync_invoices()

    assert result.success is True  # the RUN did not fail
    assert len(result.errors) == 1
    assert "Sub invoice inv-raw-1" in result.errors[0]
    assert harness._sync_single_invoice.call_count == 1  # good row synced
    # Watermark is held at the failed row (min_error), not advanced past it.
    advanced_to = harness._advance_sync_watermark.call_args.args[1]
    assert advanced_to == datetime.fromisoformat("2026-07-01T10:00:00+00:00")


@pytest.mark.parametrize("bad_updated_at", [None, "not-a-timestamp"])
def test_unpositioned_parse_failure_freezes_the_watermark(
    bad_updated_at: str | None,
) -> None:
    """An UNPOSITIONED failure (missing/malformed updated_at) cannot park the
    cursor at itself — so the cursor must FREEZE at the pre-run position;
    later good rows still sync but must not advance the watermark past the
    failed row."""
    bad = _raw_invoice(total="oops")
    if bad_updated_at is None:
        del bad["updated_at"]
    else:
        bad["updated_at"] = bad_updated_at
    good_later = _raw_invoice(id="inv-ok", updated_at="2026-07-02T10:00:00+00:00")
    harness = _InvoiceLoopHarness(_feed_client([bad, good_later]))

    result = harness.sync_invoices()

    assert result.success is True  # run continues
    assert len(result.errors) == 1
    assert harness._sync_single_invoice.call_count == 1  # good row synced
    # Frozen: the watermark was NOT advanced at all this run.
    harness._advance_sync_watermark.assert_not_called()


def test_unpositioned_savepoint_failure_also_freezes_the_watermark() -> None:
    """The freeze applies to ANY unpositioned row failure, including one that
    fails inside its savepoint after a clean parse."""
    no_position = _raw_invoice(id="inv-nopos")
    del no_position["updated_at"]
    good_later = _raw_invoice(id="inv-ok", updated_at="2026-07-02T10:00:00+00:00")
    harness = _InvoiceLoopHarness(_feed_client([no_position, good_later]))
    harness._sync_single_invoice = MagicMock(
        side_effect=[ValueError("posting failed"), None]
    )

    result = harness.sync_invoices()

    assert result.success is True
    assert len(result.errors) == 1
    assert harness._sync_single_invoice.call_count == 2
    harness._advance_sync_watermark.assert_not_called()


# ---------------------------------------------------------------------------
# Supplied line amounts are validated, never rounded; only derived values round
# ---------------------------------------------------------------------------


class _LineAmountHarness(InvoiceSyncMixin):
    def __init__(self) -> None:
        self._parse_date = lambda value: None
        self._get_source_tax_rate = MagicMock()
        self._resolve_source_sales_tax_code = MagicMock()


def _line(amount: Decimal | None, **overrides: object) -> InvoiceLineRecord:
    defaults: dict = {
        "id": "l1",
        "description": "Service",
        "quantity": Decimal("3"),
        "unit_price": Decimal("3.335"),
        "amount": amount,
        "tax_rate_id": None,
    }
    defaults.update(overrides)
    return InvoiceLineRecord(**defaults)


def test_supplied_line_amount_with_excess_precision_is_rejected_not_rounded() -> None:
    # The review canary: 10.005 NGN from the wire must fail the row, never
    # become 10.01.
    harness = _LineAmountHarness()
    with pytest.raises(MoneyBoundaryError, match="minor-unit precision"):
        harness._source_line_amounts(
            _line(Decimal("10.005")),
            currency_code="NGN",
            effective_date=date(2026, 7, 1),
        )


def test_supplied_line_amount_is_admitted_exactly() -> None:
    harness = _LineAmountHarness()
    subtotal, tax, tax_code = harness._source_line_amounts(
        _line(Decimal("10.01")),
        currency_code="NGN",
        effective_date=date(2026, 7, 1),
    )
    assert (subtotal, tax, tax_code) == (Decimal("10.01"), Decimal("0"), None)


def test_omitted_line_amount_derives_and_rounds_qty_times_price() -> None:
    # Only the ERP-DERIVED quantity x unit_price may round (3 x 3.335 =
    # 10.005 → 10.01 half-up) — that value is ERP's own computation, not a
    # transmitted source fact.
    harness = _LineAmountHarness()
    subtotal, tax, tax_code = harness._source_line_amounts(
        _line(None),
        currency_code="NGN",
        effective_date=date(2026, 7, 1),
    )
    assert (subtotal, tax, tax_code) == (Decimal("10.01"), Decimal("0"), None)
