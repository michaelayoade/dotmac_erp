"""C4 regression: a change to a GL-posted payment must reverse + re-post, not
mutate the amount in place.

When a dotmac_sub payment's cash amount changes on re-sync (e.g.
``succeeded -> partially_refunded``) and it's already posted to the GL,
mutating ``CustomerPayment.amount`` in place leaves the GL at the old figure
while the subledger shows the new one. The sync now reverses the existing
journal and drops the posting link so ``post_unposted_payments`` re-posts at
the new amount — and declines to mutate at all if the reversal fails.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

from app.services.dotmac_sub.sync._payments import PaymentSyncMixin


class _Harness(PaymentSyncMixin):
    def __init__(self, db, org):
        self.db = db
        self.organization_id = org


def test_posted_amount_changed_only_when_posted_and_amount_differs():
    posted = SimpleNamespace(
        journal_entry_id=uuid.uuid4(),
        amount=Decimal("100"),
        functional_currency_amount=Decimal("100"),
    )
    # posted + amount changed -> needs reversal
    assert PaymentSyncMixin._posted_amount_changed(posted, Decimal("80"), Decimal("80"))
    # posted + functional changed only -> needs reversal
    assert PaymentSyncMixin._posted_amount_changed(
        posted, Decimal("100"), Decimal("95")
    )
    # posted but unchanged -> no reversal
    assert not PaymentSyncMixin._posted_amount_changed(
        posted, Decimal("100"), Decimal("100")
    )
    # not yet posted -> never reverses (post_unposted_payments handles it)
    unposted = SimpleNamespace(
        journal_entry_id=None,
        amount=Decimal("100"),
        functional_currency_amount=Decimal("100"),
    )
    assert not PaymentSyncMixin._posted_amount_changed(
        unposted, Decimal("80"), Decimal("80")
    )


def test_reverse_clears_posting_link_on_success(monkeypatch):
    from app.services.finance.gl import reversal

    monkeypatch.setattr(
        reversal.ReversalService,
        "create_reversal",
        staticmethod(
            lambda **_kw: SimpleNamespace(
                success=True, reversal_journal_id=uuid.uuid4()
            )
        ),
    )
    payment = SimpleNamespace(
        journal_entry_id=uuid.uuid4(),
        posting_batch_id=uuid.uuid4(),
        payment_id=uuid.uuid4(),
        created_by_user_id=uuid.uuid4(),
        dotmac_sub_id="pay-1",
    )
    harness = _Harness(db=None, org=uuid.uuid4())

    assert harness._reverse_posted_payment_gl(payment, None) is True
    # posting link cleared so post_unposted_payments re-posts at the new amount
    assert payment.journal_entry_id is None
    assert payment.posting_batch_id is None


def test_reverse_preserves_posting_link_on_failure(monkeypatch):
    from app.services.finance.gl import reversal

    monkeypatch.setattr(
        reversal.ReversalService,
        "create_reversal",
        staticmethod(lambda **_kw: SimpleNamespace(success=False, message="boom")),
    )
    original_journal = uuid.uuid4()
    payment = SimpleNamespace(
        journal_entry_id=original_journal,
        posting_batch_id=uuid.uuid4(),
        payment_id=uuid.uuid4(),
        created_by_user_id=uuid.uuid4(),
        dotmac_sub_id="pay-2",
    )
    harness = _Harness(db=None, org=uuid.uuid4())

    assert harness._reverse_posted_payment_gl(payment, None) is False
    # link intact — caller will decline the mutation rather than diverge
    assert payment.journal_entry_id == original_journal
