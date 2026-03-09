"""
Tests for expense reimbursement transfer lifecycle.

Covers the full Paystack transfer flow for expense claims:
- Intent creation (step 1)
- Transfer initiation (step 2) — immediate success, pending, and failed paths
- Webhook processing (transfer.success, transfer.failed, transfer.reversed)
- Polling task for stuck transfers
- Edge cases: race conditions, idempotency, status gate logic
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.expense.expense_claim import ExpenseClaimStatus
from app.models.finance.payments.payment_intent import (
    PaymentDirection,
    PaymentIntentStatus,
)
from app.models.finance.payments.payment_webhook import WebhookStatus
from app.services.finance.payments.payment_service import PaymentService
from app.services.finance.payments.paystack_client import PaystackConfig
from app.services.finance.payments.webhook_service import WebhookService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CFG = PaystackConfig(
    secret_key="sk_test", public_key="pk_test", webhook_secret="wh_test"
)


_SENTINEL: Any = object()


def _org_id() -> uuid.UUID:
    return uuid.uuid4()


def _make_intent(
    *,
    org_id: uuid.UUID | None = None,
    status: PaymentIntentStatus = PaymentIntentStatus.PENDING,
    direction: PaymentDirection = PaymentDirection.OUTBOUND,
    transfer_code: str | None = None,
    amount: Decimal = Decimal("50000.00"),
    source_type: str = "EXPENSE_CLAIM",
    source_id: uuid.UUID | None = None,
    transfer_recipient_code: str = "RCP_test",
    bank_account_id: Any = _SENTINEL,
    expires_at: datetime | None = None,
    created_at: datetime | None = None,
) -> SimpleNamespace:
    """Build a lightweight intent object for unit tests."""
    return SimpleNamespace(
        intent_id=uuid.uuid4(),
        organization_id=org_id or _org_id(),
        paystack_reference=f"EXP-CLM-{uuid.uuid4().hex[:8]}",
        amount=amount,
        currency_code="NGN",
        email="employee@example.com",
        direction=direction,
        bank_account_id=uuid.uuid4()
        if bank_account_id is _SENTINEL
        else bank_account_id,
        source_type=source_type,
        source_id=source_id or uuid.uuid4(),
        transfer_recipient_code=transfer_recipient_code,
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
        expires_at=expires_at or (datetime.now(UTC) + timedelta(hours=24)),
        created_at=created_at or datetime.now(UTC),
        updated_at=None,
    )


def _make_claim(
    claim_id: uuid.UUID | None = None,
    org_id: uuid.UUID | None = None,
    status: ExpenseClaimStatus = ExpenseClaimStatus.APPROVED,
) -> SimpleNamespace:
    """Build a lightweight expense claim for unit tests."""
    return SimpleNamespace(
        claim_id=claim_id or uuid.uuid4(),
        organization_id=org_id or _org_id(),
        claim_number="EXP-001",
        status=status,
        net_payable_amount=Decimal("50000.00"),
        paid_on=None,
        payment_reference=None,
        created_by_id=uuid.uuid4(),
        reimbursement_journal_id=None,
        employee_id=uuid.uuid4(),
        recipient_bank_code="058",
        recipient_bank_name="GTBank",
        recipient_account_number="0123456789",
        recipient_account_name="Jane Doe",
        recipient_name="Jane Doe",
    )


def _make_paystack_transfer_result(
    *,
    status: str = "pending",
    transfer_code: str = "TRF_test123",
    amount: int = 5000000,
    currency: str = "NGN",
) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        transfer_code=transfer_code,
        amount=amount,
        currency=currency,
    )


def _make_paystack_verify_result(
    *,
    status: str = "success",
    fee: int | None = 5000,
    reason: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        fee=fee,
        reason=reason,
    )


def _paystack_client_context(
    initiate_result: SimpleNamespace | None = None,
    verify_result: SimpleNamespace | None = None,
) -> MagicMock:
    """Return a context-manager mock for PaystackClient."""
    client = MagicMock()
    if initiate_result:
        client.initiate_transfer.return_value = initiate_result
    if verify_result:
        client.verify_transfer.return_value = verify_result
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=client)
    cm.__exit__ = MagicMock(return_value=False)
    return cm


# ===========================================================================
# 1. initiate_expense_transfer — status transitions
# ===========================================================================


class TestInitiateExpenseTransfer:
    """Tests for PaymentService.initiate_expense_transfer()."""

    def _svc(self, db: MagicMock, org_id: uuid.UUID) -> PaymentService:
        svc = PaymentService.__new__(PaymentService)
        svc.db = db
        svc.organization_id = org_id
        return svc

    # -- happy path: Paystack returns "pending" --

    def test_pending_transfer_sets_processing_and_commits(self) -> None:
        """When Paystack returns pending, intent moves to PROCESSING and is committed."""
        org_id = _org_id()
        db = MagicMock()
        claim = _make_claim(org_id=org_id)
        intent = _make_intent(org_id=org_id, source_id=claim.claim_id)

        db.scalar.return_value = claim  # for the claim lock query

        transfer_result = _make_paystack_transfer_result(status="pending")
        client_cm = _paystack_client_context(initiate_result=transfer_result)

        svc = self._svc(db, org_id)

        with patch(
            "app.services.finance.payments.payment_service.PaystackClient",
            return_value=client_cm,
        ):
            result = svc.initiate_expense_transfer(intent, _CFG)

        assert result.status == PaymentIntentStatus.PROCESSING
        assert result.transfer_code == "TRF_test123"
        # Verify commit was called (via _commit_and_refresh)
        db.commit.assert_called()
        db.refresh.assert_called_with(intent)

    # -- happy path: Paystack returns "success" (immediate completion) --

    def test_immediate_success_sets_completed_and_commits(self) -> None:
        """When Paystack returns success immediately, intent moves to COMPLETED."""
        org_id = _org_id()
        db = MagicMock()
        claim = _make_claim(org_id=org_id)
        intent = _make_intent(org_id=org_id, source_id=claim.claim_id)

        db.scalar.side_effect = [claim, None]

        # process_successful_transfer re-fetches with FOR UPDATE
        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = intent
        db.execute.return_value = execute_result
        # db.get for expense claim inside process_successful_transfer
        db.get.return_value = claim

        transfer_result = _make_paystack_transfer_result(status="success")
        client_cm = _paystack_client_context(initiate_result=transfer_result)

        svc = self._svc(db, org_id)

        with patch(
            "app.services.finance.payments.payment_service.PaystackClient",
            return_value=client_cm,
        ):
            result = svc.initiate_expense_transfer(intent, _CFG)

        assert result.status == PaymentIntentStatus.COMPLETED
        assert result.transfer_code == "TRF_test123"
        # Claim should be PAID
        assert claim.status == ExpenseClaimStatus.PAID
        # Commit must have been called
        db.commit.assert_called()

    # -- Paystack returns "failed" --

    def test_immediate_failure_sets_failed_and_commits(self) -> None:
        """When Paystack returns failed immediately, intent is FAILED and committed."""
        org_id = _org_id()
        db = MagicMock()
        claim = _make_claim(org_id=org_id)
        intent = _make_intent(org_id=org_id, source_id=claim.claim_id)

        db.scalar.return_value = claim

        transfer_result = _make_paystack_transfer_result(status="failed")
        client_cm = _paystack_client_context(initiate_result=transfer_result)

        svc = self._svc(db, org_id)

        with patch(
            "app.services.finance.payments.payment_service.PaystackClient",
            return_value=client_cm,
        ):
            result = svc.initiate_expense_transfer(intent, _CFG)

        assert result.status == PaymentIntentStatus.FAILED
        assert result.gateway_response is not None
        db.commit.assert_called()

    # -- expired intent is rejected --

    def test_expired_intent_raises(self) -> None:
        """An expired intent cannot be initiated."""
        org_id = _org_id()
        db = MagicMock()
        intent = _make_intent(
            org_id=org_id,
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )

        svc = self._svc(db, org_id)

        with pytest.raises(HTTPException) as exc:
            svc.initiate_expense_transfer(intent, _CFG)

        assert exc.value.status_code == 400
        assert "expired" in str(exc.value.detail).lower()
        assert intent.status == PaymentIntentStatus.EXPIRED

    # -- wrong direction rejected --

    def test_inbound_intent_rejected(self) -> None:
        """INBOUND intents cannot use initiate_expense_transfer."""
        org_id = _org_id()
        db = MagicMock()
        intent = _make_intent(org_id=org_id, direction=PaymentDirection.INBOUND)

        svc = self._svc(db, org_id)

        with pytest.raises(HTTPException) as exc:
            svc.initiate_expense_transfer(intent, _CFG)

        assert exc.value.status_code == 400

    # -- wrong status rejected --

    def test_non_pending_intent_rejected(self) -> None:
        """Only PENDING intents can be initiated."""
        org_id = _org_id()
        db = MagicMock()
        intent = _make_intent(org_id=org_id, status=PaymentIntentStatus.PROCESSING)

        svc = self._svc(db, org_id)

        with pytest.raises(HTTPException) as exc:
            svc.initiate_expense_transfer(intent, _CFG)

        assert exc.value.status_code == 400

    # -- missing recipient code --

    def test_missing_recipient_code_rejected(self) -> None:
        """Intent without transfer_recipient_code is rejected."""
        org_id = _org_id()
        db = MagicMock()
        intent = _make_intent(org_id=org_id, transfer_recipient_code="")
        intent.transfer_recipient_code = None

        svc = self._svc(db, org_id)

        with pytest.raises(HTTPException) as exc:
            svc.initiate_expense_transfer(intent, _CFG)

        assert exc.value.status_code == 400
        assert "recipient" in str(exc.value.detail).lower()

    # -- cancelled claim rejected --

    def test_cancelled_claim_blocks_initiation(self) -> None:
        """If the expense claim was cancelled between steps 1 and 2, initiation is blocked."""
        org_id = _org_id()
        db = MagicMock()
        claim = _make_claim(org_id=org_id, status=ExpenseClaimStatus.CANCELLED)
        intent = _make_intent(org_id=org_id, source_id=claim.claim_id)

        db.scalar.return_value = claim

        svc = self._svc(db, org_id)

        with pytest.raises(HTTPException) as exc:
            svc.initiate_expense_transfer(intent, _CFG)

        assert exc.value.status_code == 400
        assert "CANCELLED" in str(exc.value.detail)


# ===========================================================================
# 2. process_successful_transfer — status gate and claim update
# ===========================================================================


class TestProcessSuccessfulTransfer:
    """Tests for PaymentService.process_successful_transfer()."""

    def _svc(self, db: MagicMock, org_id: uuid.UUID) -> PaymentService:
        svc = PaymentService.__new__(PaymentService)
        svc.db = db
        svc.organization_id = org_id
        return svc

    def _setup_db(
        self,
        db: MagicMock,
        locked_intent: SimpleNamespace,
        claim: SimpleNamespace | None = None,
    ) -> None:
        """Wire db.execute (FOR UPDATE) and db.get (claim lookup)."""
        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = locked_intent
        db.execute.return_value = execute_result
        db.get.return_value = claim
        # _update_batch_item_status → no batch
        db.scalar.return_value = None

    # -- normal path: PROCESSING → COMPLETED --

    def test_processing_intent_completes(self) -> None:
        """A PROCESSING intent transitions to COMPLETED and claim becomes PAID."""
        org_id = _org_id()
        db = MagicMock()
        claim = _make_claim(org_id=org_id)
        intent = _make_intent(
            org_id=org_id,
            status=PaymentIntentStatus.PROCESSING,
            source_id=claim.claim_id,
            transfer_code="TRF_abc",
        )
        self._setup_db(db, intent, claim)

        svc = self._svc(db, org_id)
        svc.process_successful_transfer(
            intent=intent,
            completed_at=datetime.now(UTC),
            gateway_response={"status": "success"},
        )

        assert intent.status == PaymentIntentStatus.COMPLETED
        assert intent.paid_at is not None
        assert claim.status == ExpenseClaimStatus.PAID
        assert claim.paid_on is not None
        db.flush.assert_called()

    # -- defensive path: PENDING → COMPLETED (webhook race) --

    def test_pending_intent_also_accepted(self) -> None:
        """A PENDING intent is accepted (defensive: webhook before commit)."""
        org_id = _org_id()
        db = MagicMock()
        claim = _make_claim(org_id=org_id)
        intent = _make_intent(
            org_id=org_id,
            status=PaymentIntentStatus.PENDING,
            source_id=claim.claim_id,
        )
        self._setup_db(db, intent, claim)

        svc = self._svc(db, org_id)
        svc.process_successful_transfer(
            intent=intent,
            completed_at=datetime.now(UTC),
            gateway_response={"status": "success"},
        )

        assert intent.status == PaymentIntentStatus.COMPLETED
        assert claim.status == ExpenseClaimStatus.PAID

    # -- idempotency: already COMPLETED is a no-op --

    def test_already_completed_is_noop(self) -> None:
        """Calling process_successful_transfer on a COMPLETED intent is idempotent."""
        org_id = _org_id()
        db = MagicMock()
        intent = _make_intent(
            org_id=org_id,
            status=PaymentIntentStatus.COMPLETED,
        )
        self._setup_db(db, intent)

        svc = self._svc(db, org_id)
        # Should not raise
        svc.process_successful_transfer(
            intent=intent,
            completed_at=datetime.now(UTC),
            gateway_response={"status": "success"},
        )

        # Status unchanged, no flush
        assert intent.status == PaymentIntentStatus.COMPLETED
        db.flush.assert_not_called()

    # -- rejected statuses --

    @pytest.mark.parametrize(
        "bad_status",
        [
            PaymentIntentStatus.FAILED,
            PaymentIntentStatus.EXPIRED,
            PaymentIntentStatus.REVERSED,
            PaymentIntentStatus.ABANDONED,
        ],
    )
    def test_invalid_statuses_rejected(self, bad_status: PaymentIntentStatus) -> None:
        """FAILED / EXPIRED / REVERSED / ABANDONED intents are rejected."""
        org_id = _org_id()
        db = MagicMock()
        intent = _make_intent(org_id=org_id, status=bad_status)
        self._setup_db(db, intent)

        svc = self._svc(db, org_id)

        with pytest.raises(HTTPException) as exc:
            svc.process_successful_transfer(
                intent=intent,
                completed_at=datetime.now(UTC),
                gateway_response={"status": "success"},
            )

        assert exc.value.status_code == 400

    # -- intent not found --

    def test_missing_intent_raises_404(self) -> None:
        """If the intent disappears (deleted?), we get a 404."""
        org_id = _org_id()
        db = MagicMock()
        intent = _make_intent(org_id=org_id, status=PaymentIntentStatus.PROCESSING)

        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = None  # gone
        db.execute.return_value = execute_result

        svc = self._svc(db, org_id)

        with pytest.raises(HTTPException) as exc:
            svc.process_successful_transfer(
                intent=intent,
                completed_at=datetime.now(UTC),
                gateway_response={},
            )

        assert exc.value.status_code == 404

    # -- claim not found (orphaned intent) --

    def test_claim_not_found_still_completes_intent(self) -> None:
        """If the claim was deleted, intent is still COMPLETED (money already sent)."""
        org_id = _org_id()
        db = MagicMock()
        intent = _make_intent(
            org_id=org_id,
            status=PaymentIntentStatus.PROCESSING,
            source_id=uuid.uuid4(),
        )

        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = intent
        db.execute.return_value = execute_result
        db.get.return_value = None  # claim missing

        db.scalar.return_value = None

        svc = self._svc(db, org_id)
        svc.process_successful_transfer(
            intent=intent,
            completed_at=datetime.now(UTC),
            gateway_response={"status": "success"},
        )

        assert intent.status == PaymentIntentStatus.COMPLETED

    # -- fee recording --

    def test_fee_recorded_in_naira(self) -> None:
        """Fee in kobo is converted to Naira and stored on intent."""
        org_id = _org_id()
        db = MagicMock()
        claim = _make_claim(org_id=org_id)
        intent = _make_intent(
            org_id=org_id,
            status=PaymentIntentStatus.PROCESSING,
            source_id=claim.claim_id,
        )
        self._setup_db(db, intent, claim)

        svc = self._svc(db, org_id)
        svc.process_successful_transfer(
            intent=intent,
            completed_at=datetime.now(UTC),
            gateway_response={"status": "success"},
            fee_kobo=5375,  # ₦53.75
        )

        assert intent.fee_amount == Decimal("53.75")

    def test_zero_fee_not_recorded(self) -> None:
        """Zero or None fee leaves fee_amount as None."""
        org_id = _org_id()
        db = MagicMock()
        claim = _make_claim(org_id=org_id)
        intent = _make_intent(
            org_id=org_id,
            status=PaymentIntentStatus.PROCESSING,
            source_id=claim.claim_id,
        )
        self._setup_db(db, intent, claim)

        svc = self._svc(db, org_id)
        svc.process_successful_transfer(
            intent=intent,
            completed_at=datetime.now(UTC),
            gateway_response={},
            fee_kobo=0,
        )

        assert intent.fee_amount is None

    # -- GL posting failure doesn't block completion --

    def test_gl_posting_failure_does_not_block_completion(self) -> None:
        """If GL posting raises, the intent is still COMPLETED (money already sent)."""
        org_id = _org_id()
        db = MagicMock()
        claim = _make_claim(org_id=org_id)
        intent = _make_intent(
            org_id=org_id,
            status=PaymentIntentStatus.PROCESSING,
            source_id=claim.claim_id,
        )
        self._setup_db(db, intent, claim)

        svc = self._svc(db, org_id)

        with patch(
            "app.services.expense.expense_posting_adapter.ExpensePostingAdapter",
        ) as mock_adapter:
            mock_adapter.post_expense_reimbursement.side_effect = RuntimeError(
                "GL boom"
            )
            svc.process_successful_transfer(
                intent=intent,
                completed_at=datetime.now(UTC),
                gateway_response={},
            )

        # Intent is COMPLETED despite GL failure
        assert intent.status == PaymentIntentStatus.COMPLETED
        assert claim.status == ExpenseClaimStatus.PAID


# ===========================================================================
# 3. mark_transfer_failed
# ===========================================================================


class TestMarkTransferFailed:
    """Tests for PaymentService.mark_transfer_failed()."""

    def _svc(self, db: MagicMock, org_id: uuid.UUID) -> PaymentService:
        svc = PaymentService.__new__(PaymentService)
        svc.db = db
        svc.organization_id = org_id
        return svc

    def test_intent_set_to_failed(self) -> None:
        org_id = _org_id()
        db = MagicMock()
        intent = _make_intent(org_id=org_id, status=PaymentIntentStatus.PROCESSING)

        db.scalar.return_value = None

        svc = self._svc(db, org_id)
        svc.mark_transfer_failed(intent, "Insufficient balance")

        assert intent.status == PaymentIntentStatus.FAILED
        assert "Insufficient balance" in intent.gateway_response["error"]

    def test_paid_claim_reverted_to_approved(self) -> None:
        """If claim was somehow marked PAID before failure, revert it."""
        org_id = _org_id()
        db = MagicMock()
        claim = _make_claim(org_id=org_id, status=ExpenseClaimStatus.PAID)
        intent = _make_intent(
            org_id=org_id,
            status=PaymentIntentStatus.PROCESSING,
            source_id=claim.claim_id,
        )
        db.get.return_value = claim
        db.scalar.return_value = None

        svc = self._svc(db, org_id)
        svc.mark_transfer_failed(intent, "Bank rejected")

        assert claim.status == ExpenseClaimStatus.APPROVED
        assert claim.paid_on is None

    def test_approved_claim_not_touched(self) -> None:
        """If claim is still APPROVED, mark_transfer_failed doesn't change it."""
        org_id = _org_id()
        db = MagicMock()
        claim = _make_claim(org_id=org_id, status=ExpenseClaimStatus.APPROVED)
        intent = _make_intent(
            org_id=org_id,
            status=PaymentIntentStatus.PROCESSING,
            source_id=claim.claim_id,
        )
        db.get.return_value = claim
        db.scalar.return_value = None

        svc = self._svc(db, org_id)
        svc.mark_transfer_failed(intent, "Timeout")

        assert claim.status == ExpenseClaimStatus.APPROVED


# ===========================================================================
# 4. poll_transfer_status — fallback for missed webhooks
# ===========================================================================


class TestPollTransferStatus:
    """Tests for PaymentService.poll_transfer_status()."""

    def _svc(self, db: MagicMock, org_id: uuid.UUID) -> PaymentService:
        svc = PaymentService.__new__(PaymentService)
        svc.db = db
        svc.organization_id = org_id
        return svc

    def test_success_from_paystack_completes_intent(self) -> None:
        """Polling finds success → process_successful_transfer called."""
        org_id = _org_id()
        db = MagicMock()
        claim = _make_claim(org_id=org_id)
        intent = _make_intent(
            org_id=org_id,
            status=PaymentIntentStatus.PROCESSING,
            transfer_code="TRF_abc",
            source_id=claim.claim_id,
        )

        # FOR UPDATE re-fetch
        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = intent
        db.execute.return_value = execute_result
        db.get.return_value = claim

        db.scalar.return_value = None

        verify_result = _make_paystack_verify_result(status="success", fee=5000)
        client_cm = _paystack_client_context(verify_result=verify_result)

        svc = self._svc(db, org_id)

        with patch(
            "app.services.finance.payments.payment_service.PaystackClient",
            return_value=client_cm,
        ):
            result = svc.poll_transfer_status(intent, _CFG)

        assert result.status == PaymentIntentStatus.COMPLETED
        assert claim.status == ExpenseClaimStatus.PAID

    def test_failed_from_paystack_marks_failed(self) -> None:
        """Polling finds failure → mark_transfer_failed called."""
        org_id = _org_id()
        db = MagicMock()
        intent = _make_intent(
            org_id=org_id,
            status=PaymentIntentStatus.PROCESSING,
            transfer_code="TRF_abc",
        )
        db.get.return_value = None  # no claim lookup needed

        db.scalar.return_value = None

        verify_result = _make_paystack_verify_result(
            status="failed", reason="Insufficient funds"
        )
        client_cm = _paystack_client_context(verify_result=verify_result)

        svc = self._svc(db, org_id)

        with patch(
            "app.services.finance.payments.payment_service.PaystackClient",
            return_value=client_cm,
        ):
            result = svc.poll_transfer_status(intent, _CFG)

        assert result.status == PaymentIntentStatus.FAILED

    def test_still_pending_on_paystack_no_change(self) -> None:
        """If Paystack says still pending, intent stays PROCESSING."""
        org_id = _org_id()
        db = MagicMock()
        intent = _make_intent(
            org_id=org_id,
            status=PaymentIntentStatus.PROCESSING,
            transfer_code="TRF_abc",
        )

        verify_result = _make_paystack_verify_result(status="pending")
        client_cm = _paystack_client_context(verify_result=verify_result)

        svc = self._svc(db, org_id)

        with patch(
            "app.services.finance.payments.payment_service.PaystackClient",
            return_value=client_cm,
        ):
            result = svc.poll_transfer_status(intent, _CFG)

        assert result.status == PaymentIntentStatus.PROCESSING

    def test_reversed_from_paystack(self) -> None:
        """Polling finds reversal → process_transfer_reversal called."""
        org_id = _org_id()
        db = MagicMock()
        claim = _make_claim(org_id=org_id, status=ExpenseClaimStatus.PAID)
        intent = _make_intent(
            org_id=org_id,
            status=PaymentIntentStatus.PROCESSING,
            transfer_code="TRF_abc",
            source_id=claim.claim_id,
        )
        db.get.return_value = claim

        db.scalar.return_value = None

        verify_result = _make_paystack_verify_result(
            status="reversed", reason="Account closed"
        )
        client_cm = _paystack_client_context(verify_result=verify_result)

        svc = self._svc(db, org_id)

        with patch(
            "app.services.finance.payments.payment_service.PaystackClient",
            return_value=client_cm,
        ):
            result = svc.poll_transfer_status(intent, _CFG)

        assert result.status == PaymentIntentStatus.REVERSED

    def test_non_processing_intent_skipped(self) -> None:
        """Polling a COMPLETED intent is a no-op."""
        org_id = _org_id()
        db = MagicMock()
        intent = _make_intent(
            org_id=org_id,
            status=PaymentIntentStatus.COMPLETED,
            transfer_code="TRF_abc",
        )

        svc = self._svc(db, org_id)
        result = svc.poll_transfer_status(intent, _CFG)

        assert result.status == PaymentIntentStatus.COMPLETED

    def test_missing_transfer_code_skipped(self) -> None:
        """Polling an intent without transfer_code is a no-op."""
        org_id = _org_id()
        db = MagicMock()
        intent = _make_intent(
            org_id=org_id,
            status=PaymentIntentStatus.PROCESSING,
            transfer_code=None,
        )

        svc = self._svc(db, org_id)
        result = svc.poll_transfer_status(intent, _CFG)

        assert result.status == PaymentIntentStatus.PROCESSING

    def test_inbound_intent_rejected(self) -> None:
        """Poll rejects INBOUND intents."""
        org_id = _org_id()
        db = MagicMock()
        intent = _make_intent(
            org_id=org_id,
            status=PaymentIntentStatus.PROCESSING,
            direction=PaymentDirection.INBOUND,
            transfer_code="TRF_abc",
        )

        svc = self._svc(db, org_id)

        with pytest.raises(HTTPException):
            svc.poll_transfer_status(intent, _CFG)


# ===========================================================================
# 5. process_transfer_reversal
# ===========================================================================


class TestProcessTransferReversal:
    """Tests for PaymentService.process_transfer_reversal()."""

    def _svc(self, db: MagicMock, org_id: uuid.UUID) -> PaymentService:
        svc = PaymentService.__new__(PaymentService)
        svc.db = db
        svc.organization_id = org_id
        return svc

    def test_completed_intent_reversed_and_claim_reverted(self) -> None:
        """COMPLETED intent reversal reverts claim to APPROVED."""
        org_id = _org_id()
        db = MagicMock()
        claim = _make_claim(org_id=org_id, status=ExpenseClaimStatus.PAID)
        intent = _make_intent(
            org_id=org_id,
            status=PaymentIntentStatus.COMPLETED,
            source_id=claim.claim_id,
        )
        db.get.return_value = claim

        db.scalar.return_value = None

        svc = self._svc(db, org_id)
        svc.process_transfer_reversal(
            intent=intent,
            reversed_at=datetime.now(UTC),
            gateway_response={"status": "reversed"},
            reason="Account closed",
        )

        assert intent.status == PaymentIntentStatus.REVERSED
        assert claim.status == ExpenseClaimStatus.APPROVED
        assert claim.paid_on is None
        assert claim.payment_reference is None

    def test_already_reversed_is_noop(self) -> None:
        """Double reversal is idempotent."""
        org_id = _org_id()
        db = MagicMock()
        intent = _make_intent(org_id=org_id, status=PaymentIntentStatus.REVERSED)

        svc = self._svc(db, org_id)
        svc.process_transfer_reversal(
            intent=intent,
            reversed_at=datetime.now(UTC),
            gateway_response={},
        )

        assert intent.status == PaymentIntentStatus.REVERSED
        db.flush.assert_not_called()

    @pytest.mark.parametrize(
        "bad_status",
        [
            PaymentIntentStatus.PENDING,
            PaymentIntentStatus.FAILED,
            PaymentIntentStatus.EXPIRED,
        ],
    )
    def test_invalid_status_for_reversal(self, bad_status: PaymentIntentStatus) -> None:
        """Only COMPLETED or PROCESSING intents can be reversed."""
        org_id = _org_id()
        db = MagicMock()
        intent = _make_intent(org_id=org_id, status=bad_status)

        svc = self._svc(db, org_id)
        svc.process_transfer_reversal(
            intent=intent,
            reversed_at=datetime.now(UTC),
            gateway_response={},
        )

        # Should NOT change to REVERSED — early return
        assert intent.status == bad_status


# ===========================================================================
# 6. Webhook service — transfer event dispatching
# ===========================================================================


class TestWebhookTransferEvents:
    """Tests for WebhookService handling transfer events."""

    def test_transfer_success_validates_amount(self) -> None:
        """transfer.success webhook validates amount before processing."""
        db = MagicMock()
        svc = WebhookService(db)
        intent = _make_intent(
            amount=Decimal("100.00"),
            status=PaymentIntentStatus.PROCESSING,
        )

        # Amount mismatch: 200.00 NGN (20000 kobo) vs intent's 100.00 (10000 kobo)
        with pytest.raises(ValueError, match="Amount mismatch"):
            svc._validate_amount_and_currency(
                intent=intent,
                data={"amount": 20000, "currency": "NGN"},
                event_type="transfer.success",
            )

    def test_transfer_success_validates_currency(self) -> None:
        """transfer.success webhook validates currency."""
        db = MagicMock()
        svc = WebhookService(db)
        intent = _make_intent(amount=Decimal("100.00"))

        with pytest.raises(ValueError, match="Currency mismatch"):
            svc._validate_amount_and_currency(
                intent=intent,
                data={"amount": 10000, "currency": "USD"},
                event_type="transfer.success",
            )

    def test_amount_within_1_kobo_tolerance_accepted(self) -> None:
        """Rounding differences of 1 kobo are tolerated."""
        db = MagicMock()
        svc = WebhookService(db)
        intent = _make_intent(amount=Decimal("100.00"))

        # 10001 kobo = 100.01 NGN → 1 kobo diff from 10000 → OK
        svc._validate_amount_and_currency(
            intent=intent,
            data={"amount": 10001, "currency": "NGN"},
            event_type="transfer.success",
        )

    def test_transfer_success_dispatches_to_payment_service(self) -> None:
        """_handle_transfer_success calls process_successful_transfer."""
        db = MagicMock()
        svc = WebhookService(db)
        intent = _make_intent(
            amount=Decimal("500.00"),
            status=PaymentIntentStatus.PROCESSING,
        )

        data: dict[str, Any] = {
            "amount": 50000,
            "currency": "NGN",
            "reference": intent.paystack_reference,
            "completed_at": "2026-02-12T10:30:00.000Z",
            "fee": 2500,
        }

        with patch.object(
            PaymentService, "process_successful_transfer"
        ) as mock_process:
            svc._handle_transfer_success(intent, data)

        mock_process.assert_called_once()
        call_kwargs = mock_process.call_args
        assert (
            call_kwargs.kwargs.get("fee_kobo") == 2500
            or call_kwargs[1].get("fee_kobo") == 2500
        )

    def test_transfer_failed_dispatches_to_mark_failed(self) -> None:
        """_handle_transfer_failed calls mark_transfer_failed."""
        db = MagicMock()
        svc = WebhookService(db)
        intent = _make_intent(status=PaymentIntentStatus.PROCESSING)

        data: dict[str, Any] = {
            "reason": "Insufficient funds",
            "transfer_code": "TRF_x",
        }

        with patch.object(PaymentService, "mark_transfer_failed") as mock_fail:
            svc._handle_transfer_failed(intent, data)

        mock_fail.assert_called_once()

    def test_transfer_reversed_dispatches_to_reversal(self) -> None:
        """_handle_transfer_reversed calls process_transfer_reversal."""
        db = MagicMock()
        svc = WebhookService(db)
        intent = _make_intent(status=PaymentIntentStatus.COMPLETED)

        data: dict[str, Any] = {
            "reason": "Account frozen",
            "reversed_at": "2026-02-12T12:00:00.000Z",
        }

        with patch.object(PaymentService, "process_transfer_reversal") as mock_reverse:
            svc._handle_transfer_reversed(intent, data)

        mock_reverse.assert_called_once()


# ===========================================================================
# 7. Webhook idempotency
# ===========================================================================


class TestWebhookIdempotency:
    """Tests for duplicate webhook handling."""

    def test_duplicate_event_id_returns_existing(self) -> None:
        """Second webhook with same event_id returns DUPLICATE status."""
        db = MagicMock()
        svc = WebhookService(db)

        existing_webhook = SimpleNamespace(
            webhook_id=uuid.uuid4(),
            status=WebhookStatus.PROCESSED,
            paystack_event_id="transfer.success:REF-1",
        )
        db.scalar.return_value = existing_webhook

        client_cls = MagicMock()
        client_cls.return_value.verify_webhook_signature.return_value = True

        with patch(
            "app.services.finance.payments.webhook_service.PaystackClient",
            client_cls,
        ):
            result = svc.process_webhook(
                event_type="transfer.success",
                event_data={"reference": "REF-1"},
                paystack_config=_CFG,
                raw_payload=b"{}",
                signature="sig",
            )

        assert result.status == WebhookStatus.DUPLICATE

    def test_event_id_built_from_type_and_reference(self) -> None:
        """Event ID is deterministic: {event_type}:{reference}."""
        svc = WebhookService(MagicMock())

        event_id = svc._build_event_id(
            "transfer.success", {"reference": "EXP-CLM-abc123"}
        )

        assert event_id == "transfer.success:EXP-CLM-abc123"

    def test_event_id_handles_missing_reference(self) -> None:
        svc = WebhookService(MagicMock())

        event_id = svc._build_event_id("charge.success", {})
        assert event_id == "charge.success:"


# ===========================================================================
# 8. Edge cases: concurrent / race / boundary
# ===========================================================================


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def _svc(self, db: MagicMock, org_id: uuid.UUID) -> PaymentService:
        svc = PaymentService.__new__(PaymentService)
        svc.db = db
        svc.organization_id = org_id
        return svc

    def test_webhook_and_poll_cannot_double_complete(self) -> None:
        """If webhook sets COMPLETED, polling sees COMPLETED and skips."""
        org_id = _org_id()
        db = MagicMock()
        intent = _make_intent(
            org_id=org_id,
            status=PaymentIntentStatus.COMPLETED,
            transfer_code="TRF_done",
        )

        svc = self._svc(db, org_id)
        # poll_transfer_status checks status first
        result = svc.poll_transfer_status(intent, _CFG)
        assert result.status == PaymentIntentStatus.COMPLETED

    def test_exactly_1_kobo_tolerance_boundary(self) -> None:
        """Exactly 1 kobo difference is accepted, 2 kobo is rejected."""
        db = MagicMock()
        svc = WebhookService(db)
        intent = _make_intent(amount=Decimal("100.00"))

        # 1 kobo diff → OK
        svc._validate_amount_and_currency(
            intent, {"amount": 10001, "currency": "NGN"}, "transfer.success"
        )

        # 2 kobo diff → rejected
        with pytest.raises(ValueError, match="Amount mismatch"):
            svc._validate_amount_and_currency(
                intent, {"amount": 10002, "currency": "NGN"}, "transfer.success"
            )

    def test_fractional_amount_kobo_conversion(self) -> None:
        """Amounts like ₦12,345.67 convert correctly to kobo."""
        db = MagicMock()
        svc = WebhookService(db)
        intent = _make_intent(amount=Decimal("12345.67"))

        # 12345.67 * 100 = 1234567 kobo
        svc._validate_amount_and_currency(
            intent, {"amount": 1234567, "currency": "NGN"}, "transfer.success"
        )

    def test_non_expense_claim_source_type(self) -> None:
        """process_successful_transfer skips claim update for non-EXPENSE_CLAIM."""
        org_id = _org_id()
        db = MagicMock()
        intent = _make_intent(
            org_id=org_id,
            status=PaymentIntentStatus.PROCESSING,
            source_type="GENERAL",
            source_id=None,
        )

        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = intent
        db.execute.return_value = execute_result

        db.scalar.return_value = None

        svc = self._svc(db, org_id)
        svc.process_successful_transfer(
            intent=intent,
            completed_at=datetime.now(UTC),
            gateway_response={},
        )

        assert intent.status == PaymentIntentStatus.COMPLETED
        # db.get not called for claim
        db.get.assert_not_called()

    def test_intent_with_no_bank_account_skips_gl_posting(self) -> None:
        """If bank_account_id is None, GL posting is skipped gracefully."""
        org_id = _org_id()
        db = MagicMock()
        claim = _make_claim(org_id=org_id)
        intent = _make_intent(
            org_id=org_id,
            status=PaymentIntentStatus.PROCESSING,
            source_id=claim.claim_id,
            bank_account_id=None,
        )

        execute_result = MagicMock()
        execute_result.scalar_one_or_none.return_value = intent
        db.execute.return_value = execute_result
        db.get.return_value = claim

        db.scalar.return_value = None

        svc = self._svc(db, org_id)

        with patch(
            "app.services.expense.expense_posting_adapter.ExpensePostingAdapter",
        ) as mock_adapter:
            svc.process_successful_transfer(
                intent=intent,
                completed_at=datetime.now(UTC),
                gateway_response={},
            )

            # GL posting NOT attempted when no bank account
            mock_adapter.post_expense_reimbursement.assert_not_called()

        assert intent.status == PaymentIntentStatus.COMPLETED
        assert claim.status == ExpenseClaimStatus.PAID
