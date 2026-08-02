"""E4 money-boundary behavior of the Sub-facing schemas.

Golden-style pins for the typed Money boundary on the dotmac_sub connector
records (invoice / credit-note / payment WHT evidence) and the Sub/CRM
payables command schema: exact NGN round-trips and byte-exact serialization
where the touched schemas carry money, plus every fail-closed rejection, and
proof that a rejected row fails ITS savepoint (raises) without any DB work.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.sync.dotmac_crm import CRMPurchaseInvoicePayload
from app.services.dotmac_sub.client import (
    AllocationRecord,
    CreditNoteRecord,
    InvoiceLineRecord,
    InvoiceRecord,
    PaymentRecord,
)
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
