from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.models.finance.ar.customer_payment import PaymentMethod, PaymentStatus
from app.services.finance.ar.receipt_bulk import ARReceiptBulkService


def _make_payment(**overrides):
    defaults = {
        "payment_id": uuid4(),
        "payment_number": "RCPT-1",
        "status": PaymentStatus.PENDING,
        "journal_entry_id": None,
        "bank_reconciliation_id": None,
        "payment_method": PaymentMethod.CARD,
        "payment_date": date(2024, 1, 1),
        "customer_id": uuid4(),
        "amount": 100,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_can_delete_rejects_non_pending():
    db = MagicMock()
    svc = ARReceiptBulkService(db, uuid4(), None)
    payment = _make_payment(status=PaymentStatus.CLEARED)

    ok, msg = svc.can_delete(payment)
    assert ok is False
    assert "only PENDING receipts can be deleted" in msg


def test_can_delete_rejects_posted_and_reconciled():
    db = MagicMock()
    svc = ARReceiptBulkService(db, uuid4(), None)
    posted = _make_payment(journal_entry_id=uuid4())
    ok, msg = svc.can_delete(posted)
    assert ok is False
    assert "already posted" in msg

    reconciled = _make_payment(bank_reconciliation_id=uuid4())
    ok, msg = svc.can_delete(reconciled)
    assert ok is False
    assert "already reconciled" in msg


def test_can_delete_rejects_allocated():
    db = MagicMock()
    db.scalar.return_value = object()
    svc = ARReceiptBulkService(db, uuid4(), None)
    payment = _make_payment()

    ok, msg = svc.can_delete(payment)
    assert ok is False
    assert "has allocations" in msg


def test_can_delete_allows_clean_pending():
    db = MagicMock()
    db.scalar.return_value = None
    svc = ARReceiptBulkService(db, uuid4(), None)
    payment = _make_payment()

    ok, msg = svc.can_delete(payment)
    assert ok is True
    assert msg == ""


def test_export_value_formats_fields():
    db = MagicMock()
    svc = ARReceiptBulkService(db, uuid4(), None)
    payment = _make_payment()

    assert svc._get_export_value(payment, "status") == PaymentStatus.PENDING.value
    assert svc._get_export_value(payment, "payment_method") == PaymentMethod.CARD.value
    assert svc._get_export_value(payment, "payment_date") == "2024-01-01"
    assert svc._get_export_value(payment, "customer_name") == str(payment.customer_id)


def test_export_filename_format():
    db = MagicMock()
    svc = ARReceiptBulkService(db, uuid4(), None)
    fixed = datetime(2024, 2, 1, 10, 30, 0)

    with patch("app.services.finance.ar.receipt_bulk.datetime") as dt:
        dt.now.return_value = fixed
        dt.strftime = datetime.strftime
        filename = svc._get_export_filename()

    assert filename == "ar_receipts_export_20240201_103000.csv"
