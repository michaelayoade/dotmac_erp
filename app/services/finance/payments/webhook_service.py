"""
Webhook Service.

Handles Paystack webhook events with idempotency and audit logging.
"""

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.finance.payments.payment_intent import PaymentIntent
from app.models.finance.payments.payment_webhook import PaymentWebhook, WebhookStatus
from app.services.finance.payments.payment_service import PaymentService
from app.services.finance.payments.paystack_client import PaystackClient, PaystackConfig

logger = logging.getLogger(__name__)


class WebhookService:
    """
    Service for processing Paystack webhooks.

    Provides idempotency, signature verification, and event handling.
    """

    def __init__(self, db: Session):
        self.db = db

    def _commit_and_refresh(self, webhook: PaymentWebhook) -> None:
        self.db.commit()
        self.db.refresh(webhook)

    def process_webhook(
        self,
        event_type: str,
        event_data: dict[str, Any],
        paystack_config: PaystackConfig,
        raw_payload: bytes,
        signature: str,
    ) -> PaymentWebhook:
        """
        Process a Paystack webhook event.

        Args:
            event_type: Paystack event type (e.g., charge.success)
            event_data: Event payload data
            paystack_config: For signature verification
            raw_payload: Raw request body
            signature: X-Paystack-Signature header

        Returns:
            PaymentWebhook record

        Raises:
            ValueError: If signature verification fails
        """
        # Verify signature
        client = PaystackClient(paystack_config)
        if not client.verify_webhook_signature(raw_payload, signature):
            logger.warning("Invalid webhook signature received")
            raise ValueError("Invalid webhook signature")

        # Extract reference and build idempotency key
        reference = event_data.get("reference", "")
        event_id = self._build_event_id(event_type, event_data)

        # Check for duplicate (idempotency)
        existing = (
            self.db.query(PaymentWebhook)
            .filter(PaymentWebhook.paystack_event_id == event_id)
            .first()
        )
        if existing:
            logger.info(f"Duplicate webhook received: {event_id}")
            existing.status = WebhookStatus.DUPLICATE
            self.db.flush()
            self._commit_and_refresh(existing)
            return existing

        # Create webhook record
        webhook = PaymentWebhook(
            webhook_id=uuid4(),
            event_type=event_type,
            paystack_event_id=event_id,
            paystack_reference=reference,
            payload=event_data,
            signature=signature,
            status=WebhookStatus.RECEIVED,
        )
        self.db.add(webhook)
        try:
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            existing = (
                self.db.query(PaymentWebhook)
                .filter(PaymentWebhook.paystack_event_id == event_id)
                .first()
            )
            if existing:
                logger.info(f"Duplicate webhook received: {event_id}")
                existing.status = WebhookStatus.DUPLICATE
                self.db.flush()
                self._commit_and_refresh(existing)
                return existing
            raise

        try:
            # Find payment intent by reference
            intent: PaymentIntent | None = None
            if reference:
                intent = (
                    self.db.query(PaymentIntent)
                    .filter(PaymentIntent.paystack_reference == reference)
                    .first()
                )

            if not intent:
                logger.warning(f"No payment intent found for reference: {reference}")
                webhook.status = WebhookStatus.FAILED
                webhook.error_message = (
                    f"Payment intent not found for reference: {reference}"
                )
                self.db.flush()
                self._commit_and_refresh(webhook)
                return webhook

            # Set organization ID from intent
            webhook.organization_id = intent.organization_id

            # Update status to processing
            webhook.status = WebhookStatus.PROCESSING
            self.db.flush()

            # Handle event
            if event_type == "charge.success":
                self._handle_charge_success(intent, event_data)
            elif event_type == "charge.failed":
                self._handle_charge_failed(intent, event_data)
            elif event_type == "transfer.success":
                self._handle_transfer_success(intent, event_data)
            elif event_type == "transfer.failed":
                self._handle_transfer_failed(intent, event_data)
            elif event_type == "transfer.reversed":
                self._handle_transfer_reversed(intent, event_data)
            else:
                logger.info(f"Unhandled event type: {event_type}")

            webhook.status = WebhookStatus.PROCESSED
            webhook.processed_at = datetime.now(UTC)

        except Exception as e:
            logger.exception(f"Webhook processing error: {e}")
            webhook.status = WebhookStatus.FAILED
            webhook.error_message = str(e)[:1000]  # Truncate long errors
            webhook.retry_count += 1

        self.db.flush()
        self._commit_and_refresh(webhook)
        return webhook

    def _build_event_id(self, event_type: str, event_data: dict[str, Any]) -> str:
        """Build unique event ID for idempotency.

        Uses only event_type and reference (NOT transaction_id) because:
        - The reference is our idempotency key with Paystack
        - Transaction IDs can change if Paystack retries internally
        - We want to prevent duplicate processing of the same payment event
        """
        reference = event_data.get("reference", "")
        return f"{event_type}:{reference}"

    def _validate_amount_and_currency(
        self,
        intent: "PaymentIntent",
        data: dict[str, Any],
        event_type: str,
    ) -> None:
        """
        Validate that Paystack's reported amount/currency matches our intent.

        Args:
            intent: The payment intent we're processing
            data: Webhook event data from Paystack
            event_type: The type of event (for logging)

        Raises:
            ValueError: If amount or currency doesn't match
        """
        from decimal import ROUND_HALF_UP, Decimal

        # Paystack sends amount in kobo (smallest unit), we store in currency units
        raw_amount = data.get("amount", 0)
        try:
            paystack_amount_kobo = int(raw_amount)
        except (TypeError, ValueError):
            logger.error(
                "SECURITY: Invalid amount in %s webhook payload: %s",
                event_type,
                raw_amount,
            )
            raise ValueError("Invalid amount in webhook payload")
        paystack_currency = data.get("currency", "NGN").upper()

        # Convert our amount to kobo for comparison
        expected_amount_kobo = int(
            (Decimal(intent.amount) * Decimal("100")).to_integral_value(
                rounding=ROUND_HALF_UP
            )
        )
        expected_currency = intent.currency_code.upper()

        # Allow 1 kobo tolerance for rounding differences
        amount_diff = abs(paystack_amount_kobo - expected_amount_kobo)
        if amount_diff > 1:
            logger.error(
                f"SECURITY: Amount mismatch in {event_type}! "
                f"Expected {expected_amount_kobo} kobo, got {paystack_amount_kobo} kobo. "
                f"Intent: {intent.intent_id}, Reference: {intent.paystack_reference}"
            )
            raise ValueError(
                f"Amount mismatch: expected {expected_amount_kobo} kobo, "
                f"received {paystack_amount_kobo} kobo"
            )

        if paystack_currency != expected_currency:
            logger.error(
                f"SECURITY: Currency mismatch in {event_type}! "
                f"Expected {expected_currency}, got {paystack_currency}. "
                f"Intent: {intent.intent_id}, Reference: {intent.paystack_reference}"
            )
            raise ValueError(
                f"Currency mismatch: expected {expected_currency}, "
                f"received {paystack_currency}"
            )

    def _handle_charge_success(
        self,
        intent: PaymentIntent,
        data: dict[str, Any],
    ) -> None:
        """
        Handle successful charge event.

        Creates customer payment and updates invoice.
        """
        # Validate amount and currency match what we expected
        self._validate_amount_and_currency(intent, data, "charge.success")

        payment_svc = PaymentService(self.db, intent.organization_id)

        # Parse paid_at timestamp
        paid_at_str = data.get("paid_at")
        if paid_at_str:
            # Paystack format: "2024-01-15T10:30:00.000Z"
            try:
                paid_at = datetime.fromisoformat(paid_at_str.replace("Z", "+00:00"))
            except ValueError:
                paid_at = datetime.now(UTC)
        else:
            paid_at = datetime.now(UTC)

        channel = data.get("channel", "card")
        transaction_id = str(data.get("id", ""))

        payment_svc.process_successful_payment(
            intent=intent,
            transaction_id=transaction_id,
            paid_at=paid_at,
            gateway_response=data,
            channel=channel,
        )

        logger.info(
            f"Processed charge.success for intent {intent.intent_id}",
            extra={
                "intent_id": str(intent.intent_id),
                "transaction_id": transaction_id,
            },
        )

    def _handle_charge_failed(
        self,
        intent: PaymentIntent,
        data: dict[str, Any],
    ) -> None:
        """Handle failed charge event."""
        payment_svc = PaymentService(self.db, intent.organization_id)

        error = data.get("gateway_response", "Payment failed")
        payment_svc.mark_payment_failed(intent, error, data)

        logger.warning(
            f"Charge failed for intent {intent.intent_id}: {error}",
            extra={
                "intent_id": str(intent.intent_id),
                "error": error,
            },
        )

    def _handle_transfer_success(
        self,
        intent: PaymentIntent,
        data: dict[str, Any],
    ) -> None:
        """
        Handle successful transfer event.

        This is for outbound transfers (e.g., expense reimbursements).
        """
        # Validate amount and currency match what we expected
        self._validate_amount_and_currency(intent, data, "transfer.success")

        payment_svc = PaymentService(self.db, intent.organization_id)

        # Parse completed_at timestamp
        completed_at_str = data.get("completed_at") or data.get("updated_at")
        if completed_at_str:
            try:
                completed_at = datetime.fromisoformat(
                    completed_at_str.replace("Z", "+00:00")
                )
            except ValueError:
                completed_at = datetime.now(UTC)
        else:
            completed_at = datetime.now(UTC)

        # Extract fee from webhook payload (Paystack uses 'fee' or 'fees')
        fee_kobo = data.get("fee") or data.get("fees")

        payment_svc.process_successful_transfer(
            intent=intent,
            completed_at=completed_at,
            gateway_response=data,
            fee_kobo=fee_kobo,
        )

        logger.info(
            f"Processed transfer.success for intent {intent.intent_id}",
            extra={
                "intent_id": str(intent.intent_id),
                "transfer_code": data.get("transfer_code"),
                "fee_kobo": fee_kobo,
            },
        )

    def _handle_transfer_failed(
        self,
        intent: PaymentIntent,
        data: dict[str, Any],
    ) -> None:
        """Handle failed transfer event."""
        payment_svc = PaymentService(self.db, intent.organization_id)

        error = data.get("reason") or data.get("message") or "Transfer failed"
        payment_svc.mark_transfer_failed(intent, error, data)

        logger.warning(
            f"Transfer failed for intent {intent.intent_id}: {error}",
            extra={
                "intent_id": str(intent.intent_id),
                "error": error,
                "transfer_code": data.get("transfer_code"),
            },
        )

    def _handle_transfer_reversed(
        self,
        intent: PaymentIntent,
        data: dict[str, Any],
    ) -> None:
        """
        Handle transfer reversal event.

        This occurs when a completed transfer is reversed by the bank
        or Paystack (e.g., account issues, compliance, etc.).
        """
        payment_svc = PaymentService(self.db, intent.organization_id)

        # Parse reversed_at timestamp
        reversed_at_str = data.get("reversed_at") or data.get("updated_at")
        if reversed_at_str:
            try:
                reversed_at = datetime.fromisoformat(
                    reversed_at_str.replace("Z", "+00:00")
                )
            except ValueError:
                reversed_at = datetime.now(UTC)
        else:
            reversed_at = datetime.now(UTC)

        reason = data.get("reason") or data.get("message") or "Transfer reversed"

        payment_svc.process_transfer_reversal(
            intent=intent,
            reversed_at=reversed_at,
            gateway_response=data,
            reason=reason,
        )

        logger.warning(
            f"Processed transfer.reversed for intent {intent.intent_id}",
            extra={
                "intent_id": str(intent.intent_id),
                "transfer_code": data.get("transfer_code"),
                "reason": reason,
            },
        )

    def get_webhook_by_event_id(self, event_id: str) -> PaymentWebhook | None:
        """Get a webhook record by event ID."""
        return (
            self.db.query(PaymentWebhook)
            .filter(PaymentWebhook.paystack_event_id == event_id)
            .first()
        )

    def retry_failed_webhook(self, webhook_id: UUID) -> PaymentWebhook:
        """
        Retry a failed webhook.

        Args:
            webhook_id: ID of the failed webhook

        Returns:
            Updated PaymentWebhook record
        """
        webhook = self.db.get(PaymentWebhook, webhook_id)
        if not webhook:
            raise ValueError(f"Webhook {webhook_id} not found")

        if webhook.status != WebhookStatus.FAILED:
            raise ValueError(f"Can only retry FAILED webhooks, got {webhook.status}")

        # Reset status and re-process
        webhook.status = WebhookStatus.RECEIVED
        webhook.error_message = None
        self.db.flush()

        # Find intent and re-process
        intent = (
            self.db.query(PaymentIntent)
            .filter(PaymentIntent.paystack_reference == webhook.paystack_reference)
            .first()
        )

        if not intent:
            webhook.status = WebhookStatus.FAILED
            webhook.error_message = "Payment intent not found"
            self.db.flush()
            self._commit_and_refresh(webhook)
            return webhook

        try:
            webhook.status = WebhookStatus.PROCESSING
            self.db.flush()

            if webhook.event_type == "charge.success":
                self._handle_charge_success(intent, webhook.payload or {})
            elif webhook.event_type == "charge.failed":
                self._handle_charge_failed(intent, webhook.payload or {})
            elif webhook.event_type == "transfer.success":
                self._handle_transfer_success(intent, webhook.payload or {})
            elif webhook.event_type == "transfer.failed":
                self._handle_transfer_failed(intent, webhook.payload or {})
            elif webhook.event_type == "transfer.reversed":
                self._handle_transfer_reversed(intent, webhook.payload or {})

            webhook.status = WebhookStatus.PROCESSED
            webhook.processed_at = datetime.now(UTC)

        except Exception as e:
            logger.exception(f"Webhook retry failed: {e}")
            webhook.status = WebhookStatus.FAILED
            webhook.error_message = str(e)[:1000]
            webhook.retry_count += 1

        self.db.flush()
        self._commit_and_refresh(webhook)
        return webhook
