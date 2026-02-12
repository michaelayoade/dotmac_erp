"""
Payment Service.

Handles payment intent creation and processing for Paystack integration.
"""

import logging
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.finance.ar.customer import Customer
from app.models.finance.ar.customer_payment import PaymentMethod
from app.models.finance.ar.invoice import Invoice, InvoiceStatus
from app.models.finance.payments.payment_intent import (
    PaymentDirection,
    PaymentIntent,
    PaymentIntentStatus,
)
from app.models.finance.payments.transfer_batch import (
    TransferBatchItem,
    TransferBatchItemStatus,
    TransferBatchStatus,
)
from app.services.common import coerce_uuid
from app.services.finance.payments.paystack_client import (
    PaystackClient,
    PaystackConfig,
    PaystackError,
)
from app.services.settings_spec import resolve_value

logger = logging.getLogger(__name__)


class PaymentService:
    """
    Service for payment operations.

    Manages payment intent lifecycle from creation through completion.
    """

    def __init__(self, db: Session, organization_id: UUID):
        self.db = db
        self.organization_id = coerce_uuid(organization_id)

    def _commit_and_refresh(self, intent: PaymentIntent) -> None:
        self.db.commit()
        self.db.refresh(intent)

    @staticmethod
    def get_intent_by_reference(
        db: Session,
        reference: str,
        organization_id: UUID | None = None,
    ) -> PaymentIntent | None:
        """Get a payment intent by reference (optionally scoped to org)."""
        query = db.query(PaymentIntent).filter(
            PaymentIntent.paystack_reference == reference
        )
        if organization_id is not None:
            query = query.filter(
                PaymentIntent.organization_id == coerce_uuid(organization_id)
            )
        return query.first()

    def create_invoice_payment_intent(
        self,
        invoice_id: UUID,
        callback_url: str,
        paystack_config: PaystackConfig,
        metadata: dict[str, Any] | None = None,
    ) -> PaymentIntent:
        """
        Create a payment intent for an invoice.

        Args:
            invoice_id: The invoice to pay
            callback_url: URL to redirect after payment
            paystack_config: Paystack credentials
            metadata: Optional additional metadata

        Returns:
            PaymentIntent with authorization URL

        Raises:
            HTTPException: If invoice is not valid for payment
        """
        inv_id = coerce_uuid(invoice_id)

        # Get invoice
        invoice = self.db.get(Invoice, inv_id)
        if not invoice:
            raise HTTPException(
                status_code=404, detail=f"Invoice {invoice_id} not found"
            )
        if invoice.organization_id != self.organization_id:
            raise HTTPException(status_code=404, detail="Invoice not found")

        # Validate invoice is payable
        payable_statuses = [
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.OVERDUE,
        ]
        if invoice.status not in payable_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Invoice with status '{invoice.status.value}' cannot be paid online",
            )

        if invoice.balance_due <= Decimal("0"):
            raise HTTPException(status_code=400, detail="Invoice is already fully paid")

        # Check for existing active payment intent to prevent duplicate payments
        active_statuses = [PaymentIntentStatus.PENDING, PaymentIntentStatus.PROCESSING]
        existing_intent = (
            self.db.query(PaymentIntent)
            .filter(
                PaymentIntent.source_type == "INVOICE",
                PaymentIntent.source_id == inv_id,
                PaymentIntent.status.in_(active_statuses),
            )
            .first()
        )
        if existing_intent:
            expires_at = existing_intent.expires_at
            if expires_at and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at and expires_at <= datetime.now(UTC):
                existing_intent.status = PaymentIntentStatus.EXPIRED
                self.db.flush()
            else:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "A payment is already in progress for this invoice "
                        f"(status: {existing_intent.status.value}). "
                        "Please wait for it to complete or check the payment history."
                    ),
                )

        # Get customer and validate email
        customer = self.db.get(Customer, invoice.customer_id)
        if not customer:
            raise HTTPException(
                status_code=400, detail="Customer not found for invoice"
            )

        # Get email from primary_contact JSONB field
        email = None
        if customer.primary_contact and isinstance(customer.primary_contact, dict):
            email = customer.primary_contact.get("email")

        if not email:
            raise HTTPException(
                status_code=400,
                detail="Customer email is required for online payment. Add email to customer's primary contact.",
            )

        # Generate unique reference
        # Format: INV-{invoice_number}-{short_uuid}
        short_uuid = uuid4().hex[:8]
        reference = f"INV-{invoice.invoice_number}-{short_uuid}"

        # Amount in kobo (Naira * 100) - use round to avoid truncation
        amount_kobo = int(
            (Decimal(invoice.balance_due) * Decimal("100")).to_integral_value(
                rounding=ROUND_HALF_UP
            )
        )

        # Build metadata
        intent_metadata = {
            "invoice_number": invoice.invoice_number,
            "invoice_id": str(inv_id),
            "customer_name": customer.legal_name or customer.trading_name,
            "customer_id": str(customer.customer_id),
        }
        if metadata:
            intent_metadata.update(metadata)

        # Get collection bank account from settings
        collection_bank_account_id = resolve_value(
            self.db, SettingDomain.payments, "paystack_collection_bank_account_id"
        )
        bank_account_uuid = None
        if collection_bank_account_id:
            try:
                bank_account_uuid = coerce_uuid(collection_bank_account_id)
            except ValueError:
                logger.warning(
                    f"Invalid collection bank account ID: {collection_bank_account_id}"
                )

        # Create payment intent
        intent = PaymentIntent(
            intent_id=uuid4(),
            organization_id=self.organization_id,
            paystack_reference=reference,
            amount=invoice.balance_due,
            currency_code=invoice.currency_code or "NGN",
            email=email,
            direction=PaymentDirection.INBOUND,
            bank_account_id=bank_account_uuid,
            source_type="INVOICE",
            source_id=inv_id,
            status=PaymentIntentStatus.PENDING,
            intent_metadata=intent_metadata,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )

        # Initialize with Paystack
        with PaystackClient(paystack_config) as client:
            result = client.initialize_transaction(
                email=email,
                amount=amount_kobo,
                reference=reference,
                callback_url=callback_url,
                metadata=intent_metadata,
                currency=intent.currency_code,
            )

            intent.paystack_access_code = result.access_code
            intent.authorization_url = result.authorization_url

        self.db.add(intent)
        self.db.flush()

        logger.info(
            f"Created payment intent {intent.intent_id} for invoice {invoice.invoice_number}",
            extra={
                "intent_id": str(intent.intent_id),
                "invoice_id": str(inv_id),
                "amount": str(invoice.balance_due),
                "reference": reference,
            },
        )

        self._commit_and_refresh(intent)
        return intent

    def verify_payment_by_reference(
        self,
        reference: str,
        paystack_config: PaystackConfig,
    ) -> PaymentIntent:
        """Verify a payment by reference with Paystack and update intent status."""
        intent = PaymentService.get_intent_by_reference(
            self.db, reference, self.organization_id
        )
        if not intent:
            raise HTTPException(status_code=404, detail="Payment not found")

        if intent.status == PaymentIntentStatus.COMPLETED:
            return intent

        try:
            with PaystackClient(paystack_config) as client:
                result = client.verify_transaction(reference)

            if result.status == "success":
                try:
                    self._validate_amount_and_currency(
                        intent=intent,
                        amount_kobo=result.amount,
                        currency=result.currency,
                        context="verify",
                    )
                except ValueError as e:
                    self.mark_payment_failed(
                        intent,
                        str(e),
                        gateway_response={
                            "status": result.status,
                            "amount": result.amount,
                            "currency": result.currency,
                            "reference": result.reference,
                        },
                    )
                    self._commit_and_refresh(intent)
                    raise HTTPException(status_code=400, detail=str(e))

                if result.paid_at:
                    try:
                        paid_at = datetime.fromisoformat(
                            result.paid_at.replace("Z", "+00:00")
                        )
                    except ValueError:
                        paid_at = datetime.now(UTC)
                else:
                    paid_at = datetime.now(UTC)

                self.process_successful_payment(
                    intent=intent,
                    transaction_id=result.transaction_id,
                    paid_at=paid_at,
                    gateway_response={
                        "status": result.status,
                        "gateway_response": result.gateway_response,
                        "channel": result.channel,
                    },
                    channel=result.channel,
                )

            elif result.status == "failed":
                self.mark_payment_failed(
                    intent,
                    result.gateway_response or "Payment failed",
                )

            elif result.status == "abandoned":
                self.mark_payment_abandoned(intent)

        except PaystackError:
            raise

        self._commit_and_refresh(intent)
        return intent

    @staticmethod
    def _validate_amount_and_currency(
        intent: PaymentIntent,
        amount_kobo: int,
        currency: str,
        context: str,
    ) -> None:
        """Validate Paystack amount/currency against our intent."""
        expected_amount_kobo = int(
            (Decimal(intent.amount) * Decimal("100")).to_integral_value(
                rounding=ROUND_HALF_UP
            )
        )
        expected_currency = intent.currency_code.upper()
        paystack_currency = (currency or "NGN").upper()

        amount_diff = abs(int(amount_kobo) - expected_amount_kobo)
        if amount_diff > 1:
            logger.error(
                "SECURITY: Amount mismatch in %s! Expected %s kobo, got %s kobo. "
                "Intent: %s, Reference: %s",
                context,
                expected_amount_kobo,
                amount_kobo,
                intent.intent_id,
                intent.paystack_reference,
            )
            raise ValueError(
                f"Amount mismatch: expected {expected_amount_kobo} kobo, "
                f"received {amount_kobo} kobo"
            )

        if paystack_currency != expected_currency:
            logger.error(
                "SECURITY: Currency mismatch in %s! Expected %s, got %s. "
                "Intent: %s, Reference: %s",
                context,
                expected_currency,
                paystack_currency,
                intent.intent_id,
                intent.paystack_reference,
            )
            raise ValueError(
                f"Currency mismatch: expected {expected_currency}, "
                f"received {paystack_currency}"
            )

    def list_pending_transfers(self) -> list[PaymentIntent]:
        """List pending outbound transfers for the organization."""
        return (
            self.db.query(PaymentIntent)
            .filter(
                PaymentIntent.organization_id == self.organization_id,
                PaymentIntent.direction == PaymentDirection.OUTBOUND,
                PaymentIntent.status.in_(
                    [
                        PaymentIntentStatus.PENDING,
                        PaymentIntentStatus.PROCESSING,
                    ]
                ),
            )
            .order_by(PaymentIntent.created_at.desc())
            .all()
        )

    def process_successful_payment(
        self,
        intent: PaymentIntent,
        transaction_id: str,
        paid_at: datetime,
        gateway_response: dict[str, Any],
        channel: str = "card",
    ) -> UUID:
        """
        Process a successful payment.

        Creates CustomerPayment, posts it, and updates the invoice.

        Args:
            intent: The payment intent
            transaction_id: Paystack transaction ID
            paid_at: When payment was made
            gateway_response: Full Paystack response
            channel: Payment channel (card, bank, ussd, etc.)

        Returns:
            customer_payment_id

        Raises:
            HTTPException: If processing fails
        """
        from sqlalchemy import select

        # Re-fetch intent with row-level lock to prevent race conditions
        # between webhook and manual verification
        locked_intent = self.db.execute(
            select(PaymentIntent)
            .where(PaymentIntent.intent_id == intent.intent_id)
            .with_for_update(nowait=False)
        ).scalar_one_or_none()

        if not locked_intent:
            raise HTTPException(
                status_code=404,
                detail="Payment intent not found",
            )

        # Check if already processed (idempotency) - using locked row
        if locked_intent.status == PaymentIntentStatus.COMPLETED:
            logger.info(f"Payment intent {locked_intent.intent_id} already completed")
            if locked_intent.customer_payment_id:
                return locked_intent.customer_payment_id
            raise HTTPException(
                status_code=400,
                detail="Payment already processed but customer_payment_id missing",
            )

        # Only process PENDING or PROCESSING intents
        if locked_intent.status not in [
            PaymentIntentStatus.PENDING,
            PaymentIntentStatus.PROCESSING,
        ]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot process payment with status '{locked_intent.status.value}'",
            )

        # Update status to PROCESSING using the locked intent
        locked_intent.status = PaymentIntentStatus.PROCESSING
        self.db.flush()

        # Use locked_intent from here on
        intent = locked_intent

        # Validate source
        if intent.source_type != "INVOICE":
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported source type: {intent.source_type}",
            )

        invoice = self.db.get(Invoice, intent.source_id)
        if not invoice:
            raise HTTPException(
                status_code=400,
                detail=f"Invoice {intent.source_id} not found",
            )

        customer = self.db.get(Customer, invoice.customer_id)
        if not customer:
            raise HTTPException(
                status_code=400,
                detail="Customer not found",
            )

        # Map Paystack channel to PaymentMethod
        payment_method = self._map_channel_to_method(channel)

        # Create customer payment using the service
        from app.services.finance.ar.customer_payment import (
            CustomerPaymentInput,
            CustomerPaymentService,
            PaymentAllocationInput,
        )

        # We need a user ID for the payment creation
        # Use a system user or the customer's created_by_user_id
        system_user_id = invoice.created_by_user_id

        payment_input = CustomerPaymentInput(
            customer_id=customer.customer_id,
            payment_date=paid_at.date(),
            payment_method=payment_method,
            currency_code=intent.currency_code,
            amount=intent.amount,
            bank_account_id=intent.bank_account_id,  # Paystack settlement account
            reference=intent.paystack_reference,
            description=f"Paystack payment for {invoice.invoice_number}",
            correlation_id=str(intent.intent_id),
            allocations=[
                PaymentAllocationInput(
                    invoice_id=invoice.invoice_id,
                    amount=min(intent.amount, invoice.balance_due),
                )
            ],
        )

        try:
            payment = CustomerPaymentService.create_payment(
                db=self.db,
                organization_id=self.organization_id,
                input=payment_input,
                created_by_user_id=system_user_id,
            )

            # Auto-post if bank account is configured
            if intent.bank_account_id:
                try:
                    CustomerPaymentService.post_payment(
                        db=self.db,
                        organization_id=self.organization_id,
                        payment_id=payment.payment_id,
                        posted_by_user_id=system_user_id,
                        posting_date=paid_at.date(),
                    )
                    logger.info(
                        f"Auto-posted Paystack payment {payment.payment_id} to GL",
                        extra={"payment_id": str(payment.payment_id)},
                    )
                except Exception as post_error:
                    # Log but don't fail - payment is still recorded
                    logger.warning(
                        f"Failed to auto-post payment {payment.payment_id}: {post_error}",
                        extra={
                            "payment_id": str(payment.payment_id),
                            "error": str(post_error),
                        },
                    )
            else:
                logger.info(
                    f"Paystack payment {payment.payment_id} created but not posted - "
                    "no settlement bank account configured",
                )

            # Update intent
            intent.status = PaymentIntentStatus.COMPLETED
            intent.customer_payment_id = payment.payment_id
            intent.paystack_transaction_id = transaction_id
            intent.paid_at = paid_at
            intent.gateway_response = gateway_response

            self.db.flush()

            logger.info(
                f"Processed payment {payment.payment_id} for intent {intent.intent_id}",
                extra={
                    "payment_id": str(payment.payment_id),
                    "intent_id": str(intent.intent_id),
                    "invoice_id": str(invoice.invoice_id),
                    "amount": str(intent.amount),
                },
            )

            return payment.payment_id

        except Exception as e:
            logger.exception(f"Failed to process payment for intent {intent.intent_id}")
            intent.status = PaymentIntentStatus.FAILED
            intent.gateway_response = {
                "error": str(e),
                "original_response": gateway_response,
            }
            self.db.flush()
            raise

    def mark_payment_failed(
        self,
        intent: PaymentIntent,
        error_message: str,
        gateway_response: dict[str, Any] | None = None,
    ) -> None:
        """
        Mark a payment intent as failed.

        Args:
            intent: The payment intent
            error_message: Error description
            gateway_response: Optional Paystack response
        """
        intent.status = PaymentIntentStatus.FAILED
        intent.gateway_response = {
            "error": error_message,
            **(gateway_response or {}),
        }
        self.db.flush()

        logger.warning(
            f"Payment intent {intent.intent_id} failed: {error_message}",
            extra={
                "intent_id": str(intent.intent_id),
                "error": error_message,
            },
        )

    def mark_payment_abandoned(self, intent: PaymentIntent) -> None:
        """Mark a payment intent as abandoned (user didn't complete)."""
        intent.status = PaymentIntentStatus.ABANDONED
        self.db.flush()

        logger.info(f"Payment intent {intent.intent_id} abandoned")

    def get_intent_by_id(self, intent_id: UUID) -> PaymentIntent | None:
        """Get a payment intent by ID."""
        intent = self.db.get(PaymentIntent, coerce_uuid(intent_id))
        if intent and intent.organization_id != self.organization_id:
            return None
        return intent

    @staticmethod
    def _map_channel_to_method(channel: str) -> PaymentMethod:
        """Map Paystack channel to AR PaymentMethod."""
        channel_map = {
            "card": PaymentMethod.CARD,
            "bank": PaymentMethod.BANK_TRANSFER,
            "ussd": PaymentMethod.MOBILE_MONEY,
            "mobile_money": PaymentMethod.MOBILE_MONEY,
            "bank_transfer": PaymentMethod.BANK_TRANSFER,
            "qr": PaymentMethod.MOBILE_MONEY,
        }
        return channel_map.get(channel.lower(), PaymentMethod.CARD)

    # =========================================================================
    # Expense Reimbursement (Outbound Transfer) Methods
    # =========================================================================

    def create_expense_payment_intent(
        self,
        expense_claim_id: UUID,
        paystack_config: PaystackConfig,
        recipient_bank_code: str,
        recipient_account_number: str,
        metadata: dict[str, Any] | None = None,
    ) -> PaymentIntent:
        """
        Create a payment intent for expense reimbursement via Paystack Transfer.

        Args:
            expense_claim_id: The expense claim to reimburse
            paystack_config: Paystack credentials
            recipient_bank_code: Employee's bank code
            recipient_account_number: Employee's bank account number
            metadata: Optional additional metadata

        Returns:
            PaymentIntent with transfer details

        Raises:
            HTTPException: If expense claim is not valid for payment
        """
        from app.models.expense.expense_claim import ExpenseClaim, ExpenseClaimStatus

        claim_id = coerce_uuid(expense_claim_id)
        should_commit = False

        # Check for existing active payment intent (idempotency check)
        active_statuses = [
            PaymentIntentStatus.PENDING,
            PaymentIntentStatus.PROCESSING,
        ]
        existing_intent = self.db.scalar(
            select(PaymentIntent).where(
                PaymentIntent.source_type == "EXPENSE_CLAIM",
                PaymentIntent.source_id == claim_id,
                PaymentIntent.status.in_(active_statuses),
            )
        )

        if existing_intent:
            # Check if expired
            expires_at = existing_intent.expires_at
            if expires_at and expires_at <= datetime.now(UTC):
                # Mark as expired and allow new intent
                existing_intent.status = PaymentIntentStatus.EXPIRED
                self.db.flush()
                should_commit = True
                logger.info(
                    f"Expired stale payment intent {existing_intent.intent_id} for claim {claim_id}"
                )
            else:
                # Return existing active intent
                logger.info(
                    f"Returning existing payment intent {existing_intent.intent_id} for claim {claim_id}"
                )
                return existing_intent

        # Verify transfers are enabled
        transfers_enabled = resolve_value(
            self.db, SettingDomain.payments, "paystack_transfers_enabled"
        )
        if not transfers_enabled:
            raise HTTPException(
                status_code=400,
                detail="Paystack transfers are not enabled",
            )

        # Get expense claim with row-level lock to prevent race conditions
        claim = self.db.scalar(
            select(ExpenseClaim)
            .where(ExpenseClaim.claim_id == claim_id)
            .with_for_update(nowait=False)
        )
        if not claim:
            raise HTTPException(
                status_code=404, detail=f"Expense claim {expense_claim_id} not found"
            )
        if claim.organization_id != self.organization_id:
            raise HTTPException(status_code=404, detail="Expense claim not found")

        # Validate claim is approved and ready for payment
        if claim.status != ExpenseClaimStatus.APPROVED:
            raise HTTPException(
                status_code=400,
                detail=f"Expense claim with status '{claim.status.value}' cannot be paid",
            )

        if claim.net_payable_amount is None or claim.net_payable_amount <= Decimal("0"):
            raise HTTPException(
                status_code=400, detail="No amount payable for this claim"
            )

        # Get employee for recipient details
        from app.models.people.hr.employee import Employee

        employee = self.db.get(Employee, claim.employee_id)
        if not employee:
            raise HTTPException(
                status_code=400, detail="Employee not found for expense claim"
            )

        email = employee.work_email or employee.personal_email
        if not email:
            raise HTTPException(
                status_code=400,
                detail="Employee email is required for transfer notification",
            )

        # Get transfer bank account from settings (source of funds)
        transfer_bank_account_id = resolve_value(
            self.db, SettingDomain.payments, "paystack_transfer_bank_account_id"
        )
        bank_account_uuid = None
        if transfer_bank_account_id:
            try:
                bank_account_uuid = coerce_uuid(transfer_bank_account_id)
            except ValueError:
                logger.warning(
                    f"Invalid transfer bank account ID: {transfer_bank_account_id}"
                )

        # Generate unique reference
        short_uuid = uuid4().hex[:8]
        reference = f"EXP-{claim.claim_number}-{short_uuid}"

        # Amount in kobo (Naira * 100) - use round to avoid truncation
        int(
            (Decimal(claim.net_payable_amount) * Decimal("100")).to_integral_value(
                rounding=ROUND_HALF_UP
            )
        )

        # Build metadata
        intent_metadata = {
            "claim_number": claim.claim_number,
            "claim_id": str(claim_id),
            "employee_name": employee.full_name,
            "employee_id": str(employee.employee_id),
        }
        if metadata:
            intent_metadata.update(metadata)

        # Verify account and create transfer recipient with Paystack
        with PaystackClient(paystack_config) as client:
            # Resolve account to get verified name
            account_info = client.resolve_account(
                account_number=recipient_account_number,
                bank_code=recipient_bank_code,
            )

            # Create transfer recipient
            recipient = client.create_transfer_recipient(
                name=account_info.account_name,
                account_number=recipient_account_number,
                bank_code=recipient_bank_code,
                currency="NGN",
                description=f"Expense reimbursement for {employee.full_name}",
                metadata=intent_metadata,
            )

        # Store verified account name on claim for audit trail
        claim.recipient_account_name = account_info.account_name
        self.db.flush()
        should_commit = True

        # Create payment intent
        intent = PaymentIntent(
            intent_id=uuid4(),
            organization_id=self.organization_id,
            paystack_reference=reference,
            amount=claim.net_payable_amount,
            currency_code="NGN",
            email=email,
            direction=PaymentDirection.OUTBOUND,
            bank_account_id=bank_account_uuid,
            source_type="EXPENSE_CLAIM",
            source_id=claim_id,
            transfer_recipient_code=recipient.recipient_code,
            recipient_bank_code=recipient_bank_code,
            recipient_account_number=recipient_account_number,
            recipient_account_name=account_info.account_name,
            status=PaymentIntentStatus.PENDING,
            intent_metadata=intent_metadata,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )

        self.db.add(intent)
        self.db.flush()
        should_commit = True

        logger.info(
            f"Created expense payment intent {intent.intent_id} for claim {claim.claim_number}",
            extra={
                "intent_id": str(intent.intent_id),
                "claim_id": str(claim_id),
                "amount": str(claim.net_payable_amount),
                "reference": reference,
                "recipient_code": recipient.recipient_code,
            },
        )

        if should_commit:
            self._commit_and_refresh(intent)
        return intent

    def initiate_expense_transfer(
        self,
        intent: PaymentIntent,
        paystack_config: PaystackConfig,
    ) -> PaymentIntent:
        """
        Initiate the actual Paystack transfer for an expense reimbursement.

        This is called after the payment intent is created and approved.

        Args:
            intent: The payment intent with transfer details
            paystack_config: Paystack credentials

        Returns:
            Updated PaymentIntent with transfer_code

        Raises:
            HTTPException: If transfer initiation fails
        """
        from app.models.expense.expense_claim import ExpenseClaim, ExpenseClaimStatus

        if intent.direction != PaymentDirection.OUTBOUND:
            raise HTTPException(
                status_code=400,
                detail="Can only initiate transfer for OUTBOUND payments",
            )

        if intent.status != PaymentIntentStatus.PENDING:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot initiate transfer with status '{intent.status.value}'",
            )

        if not intent.transfer_recipient_code:
            raise HTTPException(
                status_code=400,
                detail="Transfer recipient code is missing",
            )

        # Check intent expiration
        if intent.expires_at and intent.expires_at <= datetime.now(UTC):
            intent.status = PaymentIntentStatus.EXPIRED
            self.db.flush()
            self._commit_and_refresh(intent)
            raise HTTPException(
                status_code=400,
                detail="Payment intent has expired. Please create a new one.",
            )

        # Lock the expense claim to prevent concurrent modifications
        # This prevents race conditions where claim is cancelled while transfer is in progress
        if intent.source_type == "EXPENSE_CLAIM" and intent.source_id:
            locked_claim = self.db.scalar(
                select(ExpenseClaim)
                .where(ExpenseClaim.claim_id == intent.source_id)
                .with_for_update(nowait=False)
            )
            if not locked_claim:
                raise HTTPException(
                    status_code=404,
                    detail="Expense claim not found",
                )
            if locked_claim.status != ExpenseClaimStatus.APPROVED:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot initiate transfer - claim status is '{locked_claim.status.value}'",
                )

        # Amount in kobo - use round to avoid truncation
        amount_kobo = int(
            (Decimal(intent.amount) * Decimal("100")).to_integral_value(
                rounding=ROUND_HALF_UP
            )
        )

        # Initiate the transfer
        with PaystackClient(paystack_config) as client:
            result = client.initiate_transfer(
                amount=amount_kobo,
                recipient_code=intent.transfer_recipient_code,
                reference=intent.paystack_reference,
                reason=f"Expense reimbursement: {(intent.intent_metadata or {}).get('claim_number', '')}",
                currency=intent.currency_code,
            )

        # Update intent with transfer code
        intent.transfer_code = result.transfer_code

        # Check immediate status from Paystack response
        # Some transfers complete instantly, no need to wait for webhook
        if result.status == "success":
            logger.info(
                f"Transfer {result.transfer_code} completed immediately for intent {intent.intent_id}",
                extra={
                    "intent_id": str(intent.intent_id),
                    "transfer_code": result.transfer_code,
                    "status": result.status,
                },
            )
            # Process as successful immediately
            intent.status = (
                PaymentIntentStatus.PROCESSING
            )  # Set first for the lock check
            self.db.flush()
            self.process_successful_transfer(
                intent=intent,
                completed_at=datetime.now(UTC),
                gateway_response={
                    "immediate": True,
                    "transfer_code": result.transfer_code,
                    "status": result.status,
                    "amount": result.amount,
                    "currency": result.currency,
                },
                fee_kobo=None,  # Fee comes in webhook or verify
            )
        elif result.status == "failed":
            logger.warning(
                f"Transfer {result.transfer_code} failed immediately for intent {intent.intent_id}",
                extra={
                    "intent_id": str(intent.intent_id),
                    "transfer_code": result.transfer_code,
                    "status": result.status,
                },
            )
            intent.status = PaymentIntentStatus.FAILED
            intent.gateway_response = {
                "immediate": True,
                "transfer_code": result.transfer_code,
                "status": result.status,
            }
            self.db.flush()
        else:
            # Status is "pending" or other - wait for webhook
            intent.status = PaymentIntentStatus.PROCESSING
            self.db.flush()
            logger.info(
                f"Initiated transfer {result.transfer_code} for intent {intent.intent_id} (status: {result.status})",
                extra={
                    "intent_id": str(intent.intent_id),
                    "transfer_code": result.transfer_code,
                    "amount": str(intent.amount),
                    "status": result.status,
                },
            )

        # CRITICAL: Commit after Paystack transfer is initiated.  The money
        # has already left (or is in-flight), so DB must reflect transfer_code
        # and updated status.  Without this commit the session closes without
        # persisting, leaving the intent PENDING with no transfer_code — which
        # causes webhooks to be rejected and the polling task to miss it.
        self._commit_and_refresh(intent)

        return intent

    def process_successful_transfer(
        self,
        intent: PaymentIntent,
        completed_at: datetime,
        gateway_response: dict[str, Any],
        fee_kobo: int | None = None,
    ) -> None:
        """
        Process a successful transfer (expense reimbursement).

        Updates the expense claim status to PAID, posts to GL, and records fees.

        Args:
            intent: The payment intent
            completed_at: When transfer completed
            gateway_response: Full Paystack response
            fee_kobo: Transfer fee in kobo (smallest currency unit)
        """
        from sqlalchemy import select

        from app.models.expense.expense_claim import ExpenseClaim, ExpenseClaimStatus

        # Re-fetch intent with row-level lock to prevent race conditions
        # between webhook and manual polling
        locked_intent = self.db.execute(
            select(PaymentIntent)
            .where(PaymentIntent.intent_id == intent.intent_id)
            .with_for_update(nowait=False)
        ).scalar_one_or_none()

        if not locked_intent:
            logger.warning(
                "Transfer intent %s not found during processing",
                intent.intent_id,
            )
            raise HTTPException(status_code=404, detail="Transfer intent not found")

        # Check if already processed (using locked row)
        if locked_intent.status == PaymentIntentStatus.COMPLETED:
            logger.info(f"Transfer intent {locked_intent.intent_id} already completed")
            return

        # Accept PROCESSING (normal) and PENDING (defensive: webhook arrived
        # before the initiate route committed, or commit was lost).  Once
        # Paystack confirms success we must honour it regardless.
        if locked_intent.status not in (
            PaymentIntentStatus.PROCESSING,
            PaymentIntentStatus.PENDING,
        ):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot complete transfer with status '{locked_intent.status.value}'",
            )

        # Use locked_intent from here on
        intent = locked_intent

        # Update expense claim status
        claim = None
        if intent.source_type == "EXPENSE_CLAIM" and intent.source_id:
            claim = self.db.get(ExpenseClaim, intent.source_id)
            if claim:
                claim.status = ExpenseClaimStatus.PAID
                claim.paid_on = completed_at.date()
                claim.payment_reference = intent.paystack_reference
            else:
                logger.warning(
                    f"Expense claim not found for transfer intent {intent.intent_id}. "
                    f"source_id={intent.source_id}. Payment marked complete but claim not updated."
                )

        # Store fee amount (convert from kobo to Naira)
        fee_amount = None
        if fee_kobo and fee_kobo > 0:
            fee_amount = Decimal(fee_kobo) / Decimal("100")
            intent.fee_amount = fee_amount

        # Update intent
        intent.status = PaymentIntentStatus.COMPLETED
        intent.paid_at = completed_at
        intent.gateway_response = gateway_response

        self.db.flush()

        # Update batch item if this transfer is part of a batch
        self._update_batch_item_status(
            intent=intent,
            status=TransferBatchItemStatus.COMPLETED,
            completed_at=completed_at,
            fee_amount=fee_amount,
        )

        # Auto-post reimbursement to GL if bank account is configured
        system_user_id = None
        if claim and intent.bank_account_id:
            try:
                from app.services.expense.expense_posting_adapter import (
                    ExpensePostingAdapter,
                )

                # Get a system user ID for posting
                system_user_id = claim.created_by_id
                if not system_user_id:
                    logger.warning(
                        "Expense reimbursement not posted - missing user ID",
                        extra={"claim_id": str(claim.claim_id)},
                    )
                else:
                    posting_result = ExpensePostingAdapter.post_expense_reimbursement(
                        db=self.db,
                        organization_id=self.organization_id,
                        claim_id=claim.claim_id,
                        posting_date=completed_at.date(),
                        posted_by_user_id=system_user_id,
                        bank_account_id=intent.bank_account_id,
                        payment_reference=intent.paystack_reference,
                        correlation_id=str(intent.intent_id),
                    )

                    if posting_result.success:
                        logger.info(
                            f"Auto-posted expense reimbursement {claim.claim_number} to GL",
                            extra={
                                "claim_id": str(claim.claim_id),
                                "journal_entry_id": str(
                                    posting_result.journal_entry_id
                                ),
                            },
                        )
                    else:
                        logger.warning(
                            f"Failed to auto-post expense reimbursement: {posting_result.message}",
                            extra={"claim_id": str(claim.claim_id)},
                        )
            except Exception as post_error:
                # Log but don't fail - payment is still recorded
                logger.warning(
                    f"Failed to auto-post reimbursement for claim {claim.claim_id}: {post_error}",
                    extra={"claim_id": str(claim.claim_id), "error": str(post_error)},
                )
        elif claim and not intent.bank_account_id:
            logger.info(
                f"Expense reimbursement {claim.claim_number} not posted to GL - "
                "no transfer bank account configured in payment settings",
            )

        # Post transfer fee to GL if fee account is configured
        if fee_amount and fee_amount > Decimal("0") and intent.bank_account_id:
            self._post_transfer_fee(
                intent=intent,
                fee_amount=fee_amount,
                posting_date=completed_at.date(),
                system_user_id=system_user_id
                or (claim.created_by_id if claim else None),
            )

        logger.info(
            f"Processed successful transfer for intent {intent.intent_id}",
            extra={
                "intent_id": str(intent.intent_id),
                "source_type": intent.source_type,
                "source_id": str(intent.source_id) if intent.source_id else None,
                "fee_amount": str(fee_amount) if fee_amount else None,
            },
        )

    def _post_transfer_fee(
        self,
        intent: PaymentIntent,
        fee_amount: Decimal,
        posting_date,
        system_user_id: UUID | None,
    ) -> None:
        """
        Post transfer fee to GL if fee account is configured.

        Args:
            intent: Payment intent with fee details
            fee_amount: Fee amount in currency units
            posting_date: Date for posting
            system_user_id: User ID for audit trail
        """
        # Get fee expense account from settings
        fee_account_id = resolve_value(
            self.db, SettingDomain.payments, "paystack_transfer_fee_account_id"
        )

        if not fee_account_id:
            logger.debug(
                "Transfer fee not posted - no fee account configured",
                extra={"intent_id": str(intent.intent_id), "fee": str(fee_amount)},
            )
            return

        if not system_user_id:
            logger.warning(
                "Transfer fee not posted - no user ID available",
                extra={"intent_id": str(intent.intent_id)},
            )
            return

        try:
            from app.services.expense.expense_posting_adapter import (
                ExpensePostingAdapter,
            )

            fee_account_uuid = coerce_uuid(fee_account_id)

            if intent.bank_account_id is None:
                logger.warning(
                    "Transfer fee not posted - missing bank account",
                    extra={"intent_id": str(intent.intent_id)},
                )
                return

            fee_result = ExpensePostingAdapter.post_transfer_fee(
                db=self.db,
                organization_id=self.organization_id,
                posting_date=posting_date,
                posted_by_user_id=system_user_id,
                fee_amount=fee_amount,
                bank_account_id=intent.bank_account_id,
                fee_expense_account_id=fee_account_uuid,
                reference=intent.paystack_reference,
                description=f"Paystack transfer fee: {intent.paystack_reference}",
                correlation_id=str(intent.intent_id),
            )

            if fee_result.success:
                intent.fee_journal_id = fee_result.journal_entry_id
                logger.info(
                    "Posted transfer fee to GL",
                    extra={
                        "intent_id": str(intent.intent_id),
                        "fee": str(fee_amount),
                        "journal_id": str(fee_result.journal_entry_id),
                    },
                )
            else:
                logger.warning(
                    f"Failed to post transfer fee: {fee_result.message}",
                    extra={"intent_id": str(intent.intent_id), "fee": str(fee_amount)},
                )

        except Exception as e:
            logger.warning(
                f"Error posting transfer fee: {e}",
                extra={"intent_id": str(intent.intent_id), "error": str(e)},
            )

    def _update_batch_item_status(
        self,
        intent: PaymentIntent,
        status: TransferBatchItemStatus,
        completed_at: datetime | None = None,
        fee_amount: Decimal | None = None,
        error_message: str | None = None,
    ) -> None:
        """
        Update batch item status if the intent is part of a batch.

        Also updates batch totals when items complete or fail.

        Args:
            intent: The payment intent
            status: New status for the batch item
            completed_at: When the transfer completed (for COMPLETED status)
            fee_amount: Transfer fee (for COMPLETED status)
            error_message: Error description (for FAILED status)
        """
        # Find batch item by payment intent
        batch_item = (
            self.db.query(TransferBatchItem)
            .filter(TransferBatchItem.payment_intent_id == intent.intent_id)
            .first()
        )

        if not batch_item:
            # Intent is not part of a batch
            return

        # Update batch item
        batch_item.status = status
        if completed_at:
            batch_item.completed_at = completed_at
        if fee_amount:
            batch_item.fee_amount = fee_amount
        if error_message:
            batch_item.error_message = error_message[:500] if error_message else None

        # Update batch totals
        batch = batch_item.batch
        if batch:
            batch.update_totals()

            # Update batch status based on item completion
            all_items = batch.items
            total = len(all_items)
            completed = batch.completed_count
            failed = batch.failed_count

            if completed + failed == total:
                # All items are finalized
                if failed == 0:
                    batch.status = TransferBatchStatus.COMPLETED
                elif completed == 0:
                    batch.status = TransferBatchStatus.FAILED
                else:
                    batch.status = TransferBatchStatus.PARTIALLY_COMPLETED

        self.db.flush()

        logger.info(
            f"Updated batch item for intent {intent.intent_id} to {status.value}",
            extra={
                "intent_id": str(intent.intent_id),
                "batch_item_id": str(batch_item.item_id),
                "batch_id": str(batch_item.batch_id),
            },
        )

    def mark_transfer_failed(
        self,
        intent: PaymentIntent,
        error_message: str,
        gateway_response: dict[str, Any] | None = None,
    ) -> None:
        """
        Mark a transfer intent as failed.

        Also reverts the expense claim status back to APPROVED if needed.

        Args:
            intent: The payment intent
            error_message: Error description
            gateway_response: Optional Paystack response
        """
        from app.models.expense.expense_claim import ExpenseClaim, ExpenseClaimStatus

        intent.status = PaymentIntentStatus.FAILED
        intent.gateway_response = {
            "error": error_message,
            **(gateway_response or {}),
        }

        # Revert expense claim status if it was somehow marked PAID
        if intent.source_type == "EXPENSE_CLAIM" and intent.source_id:
            claim = self.db.get(ExpenseClaim, intent.source_id)
            if claim and claim.status == ExpenseClaimStatus.PAID:
                claim.status = ExpenseClaimStatus.APPROVED
                claim.paid_on = None
                claim.payment_reference = None
                logger.info(
                    f"Reverted claim {claim.claim_number} to APPROVED due to failed transfer"
                )

        self.db.flush()

        # Update batch item if this transfer is part of a batch
        self._update_batch_item_status(
            intent=intent,
            status=TransferBatchItemStatus.FAILED,
            error_message=error_message,
        )

        logger.warning(
            f"Transfer intent {intent.intent_id} failed: {error_message}",
            extra={
                "intent_id": str(intent.intent_id),
                "error": error_message,
            },
        )

    def poll_transfer_status(
        self,
        intent: PaymentIntent,
        paystack_config: PaystackConfig,
    ) -> PaymentIntent:
        """
        Poll Paystack for transfer status (fallback for missed webhooks).

        Use this to check status of transfers stuck in PROCESSING state.

        Args:
            intent: The payment intent with transfer_code
            paystack_config: Paystack credentials

        Returns:
            Updated PaymentIntent
        """
        if intent.direction != PaymentDirection.OUTBOUND:
            raise HTTPException(
                status_code=400,
                detail="Can only poll transfer status for OUTBOUND payments",
            )

        if intent.status != PaymentIntentStatus.PROCESSING:
            logger.debug(f"Intent {intent.intent_id} not in PROCESSING state")
            return intent

        if not intent.transfer_code:
            logger.warning(f"Intent {intent.intent_id} has no transfer_code")
            return intent

        with PaystackClient(paystack_config) as client:
            result = client.verify_transfer(intent.transfer_code)

        if result.status == "success":
            self.process_successful_transfer(
                intent,
                completed_at=datetime.now(UTC),
                gateway_response={"polled": True, "transfer_status": result.status},
                fee_kobo=result.fee,
            )
        elif result.status == "failed":
            self.mark_transfer_failed(
                intent,
                error_message=f"Transfer failed: {result.reason or 'Unknown'}",
                gateway_response={"polled": True, "transfer_status": result.status},
            )
        elif result.status == "reversed":
            self.process_transfer_reversal(
                intent,
                reversed_at=datetime.now(UTC),
                gateway_response={"polled": True, "transfer_status": result.status},
                reason=result.reason,
            )
        else:
            logger.info(
                f"Transfer {intent.transfer_code} still pending: {result.status}",
            )

        return intent

    def process_transfer_reversal(
        self,
        intent: PaymentIntent,
        reversed_at: datetime,
        gateway_response: dict[str, Any],
        reason: str | None = None,
    ) -> None:
        """
        Process a transfer reversal (funds returned).

        Updates status, reverts expense claim, and creates reversal journal entries.

        Args:
            intent: The payment intent
            reversed_at: When reversal occurred
            gateway_response: Full Paystack response
            reason: Reason for reversal
        """
        from app.models.expense.expense_claim import ExpenseClaim, ExpenseClaimStatus

        # Check if already processed
        if intent.status == PaymentIntentStatus.REVERSED:
            logger.info(f"Transfer intent {intent.intent_id} already reversed")
            return

        # Can only reverse COMPLETED or PROCESSING transfers
        if intent.status not in [
            PaymentIntentStatus.COMPLETED,
            PaymentIntentStatus.PROCESSING,
        ]:
            logger.warning(
                f"Cannot reverse intent {intent.intent_id} with status '{intent.status.value}'"
            )
            return

        # Update intent status
        was_completed = intent.status == PaymentIntentStatus.COMPLETED
        intent.status = PaymentIntentStatus.REVERSED
        intent.gateway_response = {
            **(intent.gateway_response or {}),
            "reversal": gateway_response,
            "reversal_reason": reason,
            "reversed_at": reversed_at.isoformat(),
        }

        # Revert expense claim status back to APPROVED
        claim = None
        if intent.source_type == "EXPENSE_CLAIM" and intent.source_id:
            claim = self.db.get(ExpenseClaim, intent.source_id)
            if claim and claim.status == ExpenseClaimStatus.PAID:
                claim.status = ExpenseClaimStatus.APPROVED
                claim.paid_on = None
                claim.payment_reference = None
                # Clear reimbursement journal reference (will create reversal)
                # Note: We keep the original journal for audit trail

        self.db.flush()

        # Update batch item if this transfer is part of a batch
        # Reversals count as failures for batch tracking
        self._update_batch_item_status(
            intent=intent,
            status=TransferBatchItemStatus.FAILED,
            error_message=f"Transfer reversed: {reason or 'No reason provided'}",
        )

        # Create reversal journal entries if we had posted to GL
        if was_completed and intent.bank_account_id and claim:
            self._post_reversal_entries(intent, claim, reversed_at)

        logger.info(
            f"Processed transfer reversal for intent {intent.intent_id}",
            extra={
                "intent_id": str(intent.intent_id),
                "reason": reason,
                "was_completed": was_completed,
            },
        )

    def _post_reversal_entries(
        self,
        intent: PaymentIntent,
        claim,
        reversed_at: datetime,
    ) -> None:
        """
        Post reversal journal entries for a reversed transfer.

        Creates entries that reverse both the reimbursement and fee postings.
        """
        from app.services.expense.expense_posting_adapter import ExpensePostingAdapter

        system_user_id = claim.created_by_id
        if not system_user_id:
            logger.warning("Cannot post reversal entries - no user ID")
            return
        if intent.bank_account_id is None:
            logger.warning("Cannot post reversal entries - missing bank account")
            return

        # Reverse the reimbursement entry if it was posted
        if claim.reimbursement_journal_id:
            try:
                # Create a reversal entry (opposite of original)
                # Original was: Dr Employee Payable, Cr Bank
                # Reversal is: Dr Bank, Cr Employee Payable
                result = ExpensePostingAdapter.post_expense_reimbursement_reversal(
                    db=self.db,
                    organization_id=self.organization_id,
                    claim_id=claim.claim_id,
                    original_journal_id=claim.reimbursement_journal_id,
                    posting_date=reversed_at.date(),
                    posted_by_user_id=system_user_id,
                    bank_account_id=intent.bank_account_id,
                    reason=f"Transfer reversed: {intent.paystack_reference}",
                    correlation_id=str(intent.intent_id),
                )

                if result.success:
                    logger.info(
                        f"Posted reimbursement reversal for claim {claim.claim_number}",
                        extra={"journal_id": str(result.journal_entry_id)},
                    )
                else:
                    logger.warning(
                        f"Failed to post reimbursement reversal: {result.message}"
                    )

            except Exception as e:
                logger.warning(f"Error posting reversal: {e}")

        # Reverse the fee entry if it was posted
        if intent.fee_journal_id and intent.fee_amount:
            try:
                result = ExpensePostingAdapter.post_transfer_fee_reversal(
                    db=self.db,
                    organization_id=self.organization_id,
                    original_journal_id=intent.fee_journal_id,
                    posting_date=reversed_at.date(),
                    posted_by_user_id=system_user_id,
                    fee_amount=intent.fee_amount,
                    bank_account_id=intent.bank_account_id,
                    reference=intent.paystack_reference,
                    correlation_id=str(intent.intent_id),
                )

                if result.success:
                    logger.info(
                        f"Posted fee reversal for intent {intent.intent_id}",
                        extra={"journal_id": str(result.journal_entry_id)},
                    )
                else:
                    logger.warning(f"Failed to post fee reversal: {result.message}")

            except Exception as e:
                logger.warning(f"Error posting fee reversal: {e}")
