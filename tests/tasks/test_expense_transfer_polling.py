"""The stuck-transfer poller, end to end through PaymentService.

``poll_stuck_expense_transfers`` runs every two minutes against live payout
state and, until this slice, had **no tests at all** while writing
``PaymentIntent.status`` itself. These tests cover the two things that matter
about a scheduled reconciler of money movements:

1. **An ambiguous attempt converges.** A transfer whose initiation was
   ambiguous — Paystack timed out, or rejected a duplicate reference — can sit
   PENDING while holding a ``transfer_code``, meaning the money may already
   have left. The poller must drive that to a settled verdict (COMPLETED) or a
   failed one, and never leave it in limbo.

2. **A stale worker does not corrupt state that has moved.** The worker selects
   rows in one session and acts on them in another, minutes and one network
   call later. Anything it decided from the first read must be re-proved before
   it is written, or it will stamp EXPIRED over an in-flight payout and reopen
   transfers a webhook already settled.

Written against both shapes deliberately: the per-organization session mock
answers ``.all()`` (how the pre-fix worker loaded intents) as well as
``.one_or_none()`` (how ``PaymentService`` locks one), so the assertions here
say something about the old code too. On the unfixed parent, the two
convergence tests pass — they characterize behaviour that had to survive the
move into the owner — and both stale-replay tests fail: the parent expires an
intent whose transfer has already started, and burns a poll attempt on an
intent that is already COMPLETED.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

import pytest

from app.models.expense.expense_claim import ExpenseClaimStatus
from app.models.finance.payments.payment_intent import (
    PaymentDirection,
    PaymentIntentStatus,
)
from app.services.finance.payments.paystack_client import PaystackError
from app.tasks import expense as expense_tasks

ORG_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")


# ---------------------------------------------------------------------------
# Fixtures for the two objects the job touches
# ---------------------------------------------------------------------------


def _make_intent(
    *,
    status: PaymentIntentStatus,
    transfer_code: str | None,
    poll_count: int = 0,
    expires_at: datetime | None = None,
    created_at: datetime | None = None,
    source_id: uuid.UUID | None = None,
) -> Any:
    """A payment intent stand-in carrying every field the job path reads."""
    return SimpleNamespace(
        intent_id=uuid.uuid4(),
        organization_id=ORG_A,
        paystack_reference=f"EXP-CLM-{uuid.uuid4().hex[:8]}",
        amount=Decimal("50000.00"),
        currency_code="NGN",
        email="employee@example.com",
        direction=PaymentDirection.OUTBOUND,
        # None on purpose: with no settlement account the GL posting branch is
        # skipped, so these tests are about status and nothing else.
        bank_account_id=None,
        source_type="EXPENSE_CLAIM",
        source_id=source_id or uuid.uuid4(),
        transfer_recipient_code="RCP_test",
        transfer_code=transfer_code,
        recipient_bank_code="058",
        recipient_account_number="0123456789",
        recipient_account_name="Jane Doe",
        status=status,
        customer_payment_id=None,
        paystack_transaction_id=None,
        paid_at=None,
        gateway_response=None,
        fee_amount=None,
        fee_journal_id=None,
        intent_metadata={"claim_number": "EXP-001"},
        expires_at=expires_at,
        created_at=created_at or (datetime.now(UTC) - timedelta(minutes=30)),
        updated_at=None,
        poll_count=poll_count,
        last_poll_error=None,
    )


def _make_claim(claim_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        claim_id=claim_id,
        organization_id=ORG_A,
        claim_number="EXP-001",
        status=ExpenseClaimStatus.APPROVED,
        net_payable_amount=Decimal("50000.00"),
        paid_on=None,
        payment_reference=None,
        created_by_id=uuid.uuid4(),
        reimbursement_journal_id=None,
    )


def _verify_result(
    *, status: str, reason: str | None = None, fee: int | None = 5000
) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        transfer_code="TRF_amb",
        reference="EXP-CLM-ref",
        amount=5000000,
        currency="NGN",
        recipient_code="RCP_test",
        fee=fee,
        reason=reason,
        completed_at=None,
    )


class _Harness:
    """Wires the job's two session kinds to mocks and records what happened."""

    def __init__(self, *, stale_rows, stuck_rows, intent, claim) -> None:
        self.stale_rows = stale_rows
        self.stuck_rows = stuck_rows
        self.intent = intent
        self.claim = claim
        self.orgs_opened: list[uuid.UUID] = []
        self._cross_results = [stale_rows, stuck_rows]

        self.db = MagicMock(name="db:org-a")
        # `.one_or_none()` is how PaymentService locks a single intent;
        # `.all()` is how the pre-fix worker loaded them in bulk. Both are
        # answered so these tests mean something against either shape.
        self.db.scalars.return_value.one_or_none.return_value = intent
        self.db.scalars.return_value.all.return_value = [intent]
        # process_successful_transfer's FOR UPDATE re-fetch.
        self.db.execute.return_value.scalar_one_or_none.return_value = intent
        # _update_batch_item_status: this intent is not part of a batch.
        self.db.scalar.return_value = None
        self.db.get.return_value = claim

    @contextmanager
    def cross_org_session(self):
        cross_db = MagicMock(name="db:cross-org")
        rows = self._cross_results.pop(0) if self._cross_results else []
        cross_db.execute.return_value.all.return_value = rows
        yield cross_db

    @contextmanager
    def session_for_org(self, organization_id: uuid.UUID):
        self.orgs_opened.append(organization_id)
        yield self.db


@pytest.fixture
def paystack_configured():
    """Both name bindings of resolve_value: the pre-fix worker read it from
    app.services.settings_spec at call time, PaymentService holds its own."""
    values = {
        "paystack_secret_key": "sk_test",
        "paystack_public_key": "pk_test",
        "paystack_webhook_secret": "wh_test",
    }

    def _resolve(_db, _domain, key, *args, **kwargs):
        return values.get(key)

    with (
        patch("app.services.settings_spec.resolve_value", side_effect=_resolve),
        patch(
            "app.services.finance.payments.payment_service.resolve_value",
            side_effect=_resolve,
        ),
    ):
        yield


@contextmanager
def _paystack(verify_result=None, verify_error: Exception | None = None):
    client = MagicMock()
    if verify_error is not None:
        client.verify_transfer.side_effect = verify_error
    else:
        client.verify_transfer.return_value = verify_result
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=client)
    cm.__exit__ = MagicMock(return_value=False)
    with patch(
        "app.services.finance.payments.payment_service.PaystackClient",
        return_value=cm,
    ):
        yield client


@contextmanager
def _expense_mark_paid(harness: _Harness):
    def _side_effect(
        _service,
        _org_id,
        claim_id,
        *,
        payment_reference=None,
        payment_date=None,
        send_notification=True,
        skip_budget_check=False,
    ):
        claim = harness.claim
        claim.status = ExpenseClaimStatus.PAID
        claim.paid_on = payment_date
        claim.payment_reference = payment_reference
        return claim

    with patch(
        "app.services.expense.expense_service.ExpenseService.mark_paid",
        autospec=True,
        side_effect=_side_effect,
    ):
        yield


def _run(harness: _Harness, monkeypatch) -> dict:
    monkeypatch.setattr(expense_tasks, "cross_org_session", harness.cross_org_session)
    monkeypatch.setattr(expense_tasks, "session_for_org", harness.session_for_org)
    return expense_tasks.poll_stuck_expense_transfers()


# ===========================================================================
# 1. An ambiguous attempt converges
# ===========================================================================


class TestAmbiguousAttemptConverges:
    """An intent that is PENDING but already carries a transfer_code is the
    ambiguous case: initiation timed out or hit a duplicate reference, so
    Paystack may or may not be moving the money. The poller must resolve it."""

    def test_ambiguous_transfer_settles_as_completed(
        self, monkeypatch, paystack_configured
    ) -> None:
        claim_id = uuid.uuid4()
        intent = _make_intent(
            status=PaymentIntentStatus.PENDING,
            transfer_code="TRF_amb",
            source_id=claim_id,
        )
        harness = _Harness(
            stale_rows=[],
            stuck_rows=[(intent.intent_id, ORG_A)],
            intent=intent,
            claim=_make_claim(claim_id),
        )

        with (
            _paystack(verify_result=_verify_result(status="success")),
            _expense_mark_paid(harness),
        ):
            results = _run(harness, monkeypatch)

        assert intent.status == PaymentIntentStatus.COMPLETED
        assert harness.claim.status == ExpenseClaimStatus.PAID
        assert results["completed"] == 1
        assert results["intents_checked"] == 1
        assert results["errors"] == []
        harness.db.commit.assert_called()

    def test_ambiguous_transfer_settles_as_failed(
        self, monkeypatch, paystack_configured
    ) -> None:
        claim_id = uuid.uuid4()
        intent = _make_intent(
            status=PaymentIntentStatus.PENDING,
            transfer_code="TRF_amb",
            source_id=claim_id,
        )
        harness = _Harness(
            stale_rows=[],
            stuck_rows=[(intent.intent_id, ORG_A)],
            intent=intent,
            claim=_make_claim(claim_id),
        )

        with _paystack(
            verify_result=_verify_result(status="failed", reason="Insufficient funds")
        ):
            results = _run(harness, monkeypatch)

        assert intent.status == PaymentIntentStatus.FAILED
        assert harness.claim.status == ExpenseClaimStatus.APPROVED
        assert results["failed"] == 1
        harness.db.commit.assert_called()

    def test_unreachable_transfer_settles_once_the_budget_is_spent(
        self, monkeypatch, paystack_configured
    ) -> None:
        """An ambiguous transfer Paystack will not answer about still has to
        stop somewhere. The circuit breaker is the owner's decision now; the
        last attempt settles it FAILED and says why in gateway_response."""
        claim_id = uuid.uuid4()
        intent = _make_intent(
            status=PaymentIntentStatus.PROCESSING,
            transfer_code="TRF_amb",
            poll_count=9,  # one attempt short of the limit
            source_id=claim_id,
        )
        harness = _Harness(
            stale_rows=[],
            stuck_rows=[(intent.intent_id, ORG_A)],
            intent=intent,
            claim=_make_claim(claim_id),
        )

        with _paystack(verify_error=PaystackError("Request timed out")):
            results = _run(harness, monkeypatch)

        assert intent.poll_count == 10
        assert intent.status == PaymentIntentStatus.FAILED
        assert intent.gateway_response["poll_abandoned"] is True
        assert intent.gateway_response["poll_attempts"] == 10
        assert intent.last_poll_error == "Request timed out"
        assert results["abandoned"] == 1
        # An abandoned intent is a verdict, not an unresolved error.
        assert results["errors"] == []

    def test_a_retryable_failure_is_reported_and_left_alone(
        self, monkeypatch, paystack_configured
    ) -> None:
        """Below the limit nothing is settled: the money may still be moving."""
        claim_id = uuid.uuid4()
        intent = _make_intent(
            status=PaymentIntentStatus.PROCESSING,
            transfer_code="TRF_amb",
            poll_count=0,
            source_id=claim_id,
        )
        harness = _Harness(
            stale_rows=[],
            stuck_rows=[(intent.intent_id, ORG_A)],
            intent=intent,
            claim=_make_claim(claim_id),
        )

        with _paystack(verify_error=PaystackError("Request timed out")):
            results = _run(harness, monkeypatch)

        assert intent.status == PaymentIntentStatus.PROCESSING
        assert intent.poll_count == 1
        assert results["abandoned"] == 0
        assert len(results["errors"]) == 1
        assert results["errors"][0]["intent_id"] == str(intent.intent_id)


# ===========================================================================
# 2. A stale worker must not corrupt state that has moved
# ===========================================================================


class TestStaleWorkerReplay:
    """Both passes of this job read in one session and write in another. What
    they decided from the first read has to be re-proved before it is written."""

    def test_expiry_does_not_stamp_over_a_transfer_that_has_since_started(
        self, monkeypatch, paystack_configured
    ) -> None:
        """The expiry pass selects intents that are PENDING with no transfer
        code and past their expiry. Between that select and the write, an
        operator can initiate the transfer: the row is now PROCESSING and holds
        a transfer_code, and the money may be in flight.

        On the unfixed parent this test FAILS — the worker writes EXPIRED to
        every id the cross-organization select returned, without looking at the
        row again. An expired-looking intent is one nobody chases: the payout
        leaves and the claim never reconciles.
        """
        claim_id = uuid.uuid4()
        moved = _make_intent(
            status=PaymentIntentStatus.PROCESSING,
            transfer_code="TRF_started_in_the_gap",
            expires_at=datetime.now(UTC) - timedelta(hours=1),
            source_id=claim_id,
        )
        harness = _Harness(
            stale_rows=[(moved.intent_id, ORG_A)],
            stuck_rows=[],
            intent=moved,
            claim=_make_claim(claim_id),
        )

        results = _run(harness, monkeypatch)

        assert moved.status == PaymentIntentStatus.PROCESSING
        assert moved.transfer_code == "TRF_started_in_the_gap"
        assert results.get("expired", 0) == 0
        assert harness.orgs_opened == [ORG_A]

    def test_expiry_still_expires_an_intent_that_really_did_stall(
        self, monkeypatch, paystack_configured
    ) -> None:
        """The re-check must not be so strict that the pass does nothing —
        a guard that never fires is the same as no pass at all."""
        claim_id = uuid.uuid4()
        stalled = _make_intent(
            status=PaymentIntentStatus.PENDING,
            transfer_code=None,
            expires_at=datetime.now(UTC) - timedelta(hours=1),
            source_id=claim_id,
        )
        harness = _Harness(
            stale_rows=[(stalled.intent_id, ORG_A)],
            stuck_rows=[],
            intent=stalled,
            claim=_make_claim(claim_id),
        )

        results = _run(harness, monkeypatch)

        assert stalled.status == PaymentIntentStatus.EXPIRED
        assert results["expired"] == 1
        harness.db.commit.assert_called()

    def test_polling_leaves_an_intent_a_webhook_already_settled(
        self, monkeypatch, paystack_configured
    ) -> None:
        """The poll pass selects PROCESSING/PENDING intents, then makes a
        network call per intent. A webhook can settle any of them in that
        window.

        On the unfixed parent this test FAILS: the worker increments
        ``poll_count`` on an intent that is already COMPLETED and counts it as
        still pending, because it never re-reads the row it is about to act on.
        The counter is the visible symptom; the mechanism underneath is that
        the worker's whole view of the row is stale, and writing that view back
        is what reopens a settled transfer.
        """
        claim_id = uuid.uuid4()
        settled = _make_intent(
            status=PaymentIntentStatus.COMPLETED,
            transfer_code="TRF_done",
            poll_count=3,
            source_id=claim_id,
        )
        harness = _Harness(
            stale_rows=[],
            stuck_rows=[(settled.intent_id, ORG_A)],
            intent=settled,
            claim=_make_claim(claim_id),
        )

        with _paystack(verify_result=_verify_result(status="success")) as client:
            results = _run(harness, monkeypatch)

        assert settled.status == PaymentIntentStatus.COMPLETED
        assert settled.poll_count == 3, "a settled intent must not burn an attempt"
        assert results["skipped"] == 1
        assert results["completed"] == 0
        client.verify_transfer.assert_not_called()

    def test_an_unconfigured_tenant_is_skipped_without_touching_its_intents(
        self, monkeypatch
    ) -> None:
        """No Paystack keys means no verdict is obtainable, so nothing may be
        written — least of all a FAILED that reads as 'Paystack said no'."""
        claim_id = uuid.uuid4()
        intent = _make_intent(
            status=PaymentIntentStatus.PROCESSING,
            transfer_code="TRF_amb",
            source_id=claim_id,
        )
        harness = _Harness(
            stale_rows=[],
            stuck_rows=[(intent.intent_id, ORG_A)],
            intent=intent,
            claim=_make_claim(claim_id),
        )

        with (
            patch("app.services.settings_spec.resolve_value", return_value=None),
            patch(
                "app.services.finance.payments.payment_service.resolve_value",
                return_value=None,
            ),
        ):
            results = _run(harness, monkeypatch)

        assert intent.status == PaymentIntentStatus.PROCESSING
        assert intent.poll_count == 0
        assert results["completed"] == 0
        assert results["failed"] == 0
