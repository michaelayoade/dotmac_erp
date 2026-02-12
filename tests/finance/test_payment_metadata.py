from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.services.finance.banking.payment_metadata import (
    PaymentMetadata,
    resolve_payment_metadata,
    resolve_payment_metadata_batch,
)

# ---------------------------------------------------------------------------
# resolve_payment_metadata — single
# ---------------------------------------------------------------------------


def test_resolve_none_inputs() -> None:
    """Returns None when source_document_type or id is None."""
    db = MagicMock()
    assert resolve_payment_metadata(db, None, None) is None
    assert resolve_payment_metadata(db, None, uuid4()) is None
    assert resolve_payment_metadata(db, "customer_payment", None) is None


def test_resolve_unknown_type() -> None:
    """Returns None for unrecognized source document types."""
    db = MagicMock()
    assert resolve_payment_metadata(db, "journal_entry", uuid4()) is None
    assert resolve_payment_metadata(db, "expense_claim", uuid4()) is None


def test_resolve_customer_payment_happy_path() -> None:
    """Resolves a customer payment with customer name and invoice numbers."""
    db = MagicMock()
    payment_id = uuid4()
    customer_id = uuid4()

    payment = SimpleNamespace(
        payment_id=payment_id,
        customer_id=customer_id,
        receipt_number="REC-001",
    )
    customer = SimpleNamespace(
        customer_id=customer_id,
        customer_name="Acme Corp",
    )

    # First call: select CustomerPayment + Customer
    db.execute.return_value.first.return_value = (payment, customer)
    # Second call: invoice numbers query
    db.scalars.return_value.all.return_value = ["INV-001", "INV-002"]

    result = resolve_payment_metadata(db, "customer_payment", payment_id)

    assert result is not None
    assert isinstance(result, PaymentMetadata)
    assert result.source_type == "customer_payment"
    assert result.payment_id == payment_id
    assert result.counterparty_name == "Acme Corp"
    assert result.counterparty_id == customer_id
    assert result.counterparty_type == "customer"
    assert result.payment_number == "REC-001"


def test_resolve_customer_payment_not_found() -> None:
    """Returns None when the customer payment doesn't exist."""
    db = MagicMock()
    db.execute.return_value.first.return_value = None

    result = resolve_payment_metadata(db, "receipt", uuid4())
    assert result is None


def test_resolve_customer_payment_no_customer() -> None:
    """Resolves even when customer is None (deleted)."""
    db = MagicMock()
    payment_id = uuid4()
    payment = SimpleNamespace(
        payment_id=payment_id,
        payment_number="PAY-001",
    )

    db.execute.return_value.first.return_value = (payment, None)
    db.scalars.return_value.all.return_value = []

    result = resolve_payment_metadata(db, "ar_payment", payment_id)

    assert result is not None
    assert result.counterparty_name is None
    assert result.counterparty_id is None
    assert result.counterparty_type == "customer"
    assert result.payment_number == "PAY-001"


def test_resolve_supplier_payment_happy_path() -> None:
    """Resolves a supplier payment with supplier name."""
    db = MagicMock()
    payment_id = uuid4()
    supplier_id = uuid4()

    payment = SimpleNamespace(
        payment_id=payment_id,
        supplier_id=supplier_id,
        payment_number="SPAY-010",
    )
    supplier = SimpleNamespace(
        supplier_id=supplier_id,
        supplier_name="Global Supplies Ltd",
    )

    db.execute.return_value.first.return_value = (payment, supplier)

    result = resolve_payment_metadata(db, "supplier_payment", payment_id)

    assert result is not None
    assert result.source_type == "supplier_payment"
    assert result.payment_id == payment_id
    assert result.counterparty_name == "Global Supplies Ltd"
    assert result.counterparty_id == supplier_id
    assert result.counterparty_type == "supplier"
    assert result.payment_number == "SPAY-010"
    assert result.invoice_numbers == []


def test_resolve_supplier_payment_not_found() -> None:
    """Returns None when the supplier payment doesn't exist."""
    db = MagicMock()
    db.execute.return_value.first.return_value = None

    result = resolve_payment_metadata(db, "ap_payment", uuid4())
    assert result is None


def test_resolve_alias_types() -> None:
    """Recognizes alias types like 'receipt' and 'ar_payment'."""
    db = MagicMock()
    db.execute.return_value.first.return_value = None

    # These should route to customer payment resolver (returning None because not found)
    assert resolve_payment_metadata(db, "receipt", uuid4()) is None
    assert resolve_payment_metadata(db, "ar_payment", uuid4()) is None
    assert resolve_payment_metadata(db, "CUSTOMER_PAYMENT", uuid4()) is None

    # These should route to supplier payment resolver
    assert resolve_payment_metadata(db, "ap_payment", uuid4()) is None
    assert resolve_payment_metadata(db, "AP_PAYMENT", uuid4()) is None


# ---------------------------------------------------------------------------
# resolve_payment_metadata_batch
# ---------------------------------------------------------------------------


def test_batch_empty_pairs() -> None:
    """Returns empty dict for empty pairs list."""
    db = MagicMock()
    result = resolve_payment_metadata_batch(db, [])
    assert result == {}
    db.execute.assert_not_called()


def test_batch_all_none_pairs() -> None:
    """Returns empty dict when all pairs have None values."""
    db = MagicMock()
    result = resolve_payment_metadata_batch(db, [(None, None), (None, uuid4())])
    assert result == {}


def test_batch_mixed_ar_ap() -> None:
    """Batch-resolves both AR and AP payments in 2 queries."""
    db = MagicMock()

    cust_payment_id = uuid4()
    cust_id = uuid4()
    supp_payment_id = uuid4()
    supp_id = uuid4()

    cust_payment = SimpleNamespace(
        payment_id=cust_payment_id,
        customer_id=cust_id,
        receipt_number="REC-100",
    )
    customer = SimpleNamespace(
        customer_id=cust_id,
        customer_name="Customer A",
    )
    supp_payment = SimpleNamespace(
        payment_id=supp_payment_id,
        supplier_id=supp_id,
        payment_number="SPAY-200",
    )
    supplier = SimpleNamespace(
        supplier_id=supp_id,
        supplier_name="Supplier B",
    )

    # Two db.execute calls: first for customer batch, second for supplier batch
    call_count = 0

    def _execute_side(*args, **kwargs):
        nonlocal call_count
        result = MagicMock()
        if call_count == 0:
            result.all.return_value = [(cust_payment, customer)]
        else:
            result.all.return_value = [(supp_payment, supplier)]
        call_count += 1
        return result

    db.execute.side_effect = _execute_side

    pairs = [
        ("customer_payment", cust_payment_id),
        ("supplier_payment", supp_payment_id),
        (None, None),  # ignored
    ]

    result = resolve_payment_metadata_batch(db, pairs)

    assert len(result) == 2
    assert cust_payment_id in result
    assert supp_payment_id in result

    assert result[cust_payment_id].counterparty_name == "Customer A"
    assert result[cust_payment_id].counterparty_type == "customer"

    assert result[supp_payment_id].counterparty_name == "Supplier B"
    assert result[supp_payment_id].counterparty_type == "supplier"


def test_batch_customer_only() -> None:
    """Batch with only customer payments skips the AP query."""
    db = MagicMock()

    pid = uuid4()
    cid = uuid4()
    payment = SimpleNamespace(payment_id=pid, customer_id=cid, payment_number="PAY-001")
    customer = SimpleNamespace(customer_id=cid, customer_name="Test Customer")

    db.execute.return_value.all.return_value = [(payment, customer)]

    pairs = [("customer_payment", pid)]
    result = resolve_payment_metadata_batch(db, pairs)

    assert len(result) == 1
    assert result[pid].payment_number == "PAY-001"
    # Only one query (customer batch)
    assert db.execute.call_count == 1


# ---------------------------------------------------------------------------
# PaymentMetadata frozen dataclass
# ---------------------------------------------------------------------------


def test_payment_metadata_is_frozen() -> None:
    """PaymentMetadata instances are immutable."""
    meta = PaymentMetadata(
        source_type="customer_payment",
        payment_id=uuid4(),
        payment_number="PAY-001",
        counterparty_name="Test",
        counterparty_id=uuid4(),
        counterparty_type="customer",
        invoice_numbers=["INV-001"],
    )

    with pytest.raises(AttributeError):
        meta.counterparty_name = "Changed"  # type: ignore[misc]
