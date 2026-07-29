"""C3 regression: the payment sync must not double-count invoice.amount_paid.

The invoice sync sets ``amount_paid`` from dotmac_sub's authoritative
``balance_due`` (which already reflects the payment). Previously
``_apply_allocations`` *also* incremented ``amount_paid`` per allocation, so a
partially-paid invoice imported for the first time (e.g. 60 of 100 paid) got
60 + 60 = 100 and flipped to PAID. The fix makes the invoice sync the sole
owner of ``amount_paid`` / ``status``; allocations only maintain the linkage.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.models.finance.ar.invoice import InvoiceStatus
from app.services.dotmac_sub.client import AllocationRecord
from app.services.dotmac_sub.sync._payments import PaymentSyncMixin


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    """Minimal session double for _apply_allocations: no existing allocations,
    returns one invoice for the lookup, records added rows."""

    def __init__(self, invoice):
        self._invoice = invoice
        self.added: list[object] = []

    def scalars(self, _stmt):
        return _FakeScalars([])

    def scalar(self, _stmt):
        return self._invoice

    def execute(self, _stmt):
        return None

    def add(self, obj):
        self.added.append(obj)

    def get(self, _model, _pk):
        return None


class _Harness(PaymentSyncMixin):
    def __init__(self, db, org):
        self.db = db
        self.organization_id = org


def test_apply_allocations_does_not_inflate_amount_paid():
    invoice = SimpleNamespace(
        invoice_id=uuid.uuid4(),
        dotmac_sub_id="sub-inv-1",
        amount_paid=Decimal("60"),
        total_amount=Decimal("100"),
        status=InvoiceStatus.PARTIALLY_PAID,
    )
    db = _FakeDB(invoice)
    harness = _Harness(db, uuid.uuid4())

    payment = SimpleNamespace(payment_id=uuid.uuid4())
    pay = SimpleNamespace(
        allocations=[
            AllocationRecord(
                id="a1",
                payment_id="p1",
                invoice_id="sub-inv-1",
                amount=Decimal("60"),
            )
        ]
    )

    harness._apply_allocations(payment, pay, date(2026, 7, 1))

    # amount_paid / status stay as the invoice sync set them — no double count,
    # no false PAID.
    assert invoice.amount_paid == Decimal("60")
    assert invoice.status == InvoiceStatus.PARTIALLY_PAID
    # ...but the payment->invoice linkage is still recorded.
    assert len(db.added) == 1
    allocation = db.added[0]
    assert allocation.invoice_id == invoice.invoice_id
    assert allocation.allocated_amount == Decimal("60")
