"""
Payment Service.

Handles payment intent creation and processing for Paystack integration.
"""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.finance.ar.customer import Customer
from app.models.finance.ar.invoice import Invoice, InvoiceStatus
from app.models.finance.ar.customer_payment import PaymentMethod
from app.models.finance.payments.payment_intent import (
    PaymentDirection,
    PaymentIntent,
    PaymentIntentStatus,
)
from app.models.domain_settings import SettingDomain
from app.services.common import coerce_uuid
from app.services.finance.payments.paystack_client import PaystackClient, PaystackConfig
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

    def create_invoice_payment_intent(
        self,
        invoice_id: UUID,
        callback_url: str,
        paystack_config: PaystackConfig,
        metadata: Optional[dict[str, Any]] = None,
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
            raise HTTPException(status_code=404, detail=f"Invoice {invoice_id} not found")
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

        # Get customer and validate email
        customer = self.db.get(Customer, invoice.customer_id)
        if not customer:
            raise HTTPException(status_code=400, detail="Customer not found for invoice")

        email = customer.email
        if not email:
            raise HTTPException(
                status_code=400,
                detail="Customer email is required for online payment",
            )

        # Generate unique reference
        # Format: INV-{invoice_number}-{short_uuid}
        short_uuid = uuid4().hex[:8]
        reference = f"INV-{invoice.invoice_number}-{short_uuid}"

        # Amount in kobo (Naira * 100)
        amount_kobo = int(invoice.balance_due * 100)

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
                logger.warning(f"Invalid collection bank account ID: {collection_bank_account_id}")

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
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
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

        return intent

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
        # Check if already processed (idempotency)
        if intent.status == PaymentIntentStatus.COMPLETED:
            logger.info(f"Payment intent {intent.intent_id} already completed")
            if intent.customer_payment_id:
                return intent.customer_payment_id
            raise HTTPException(
                status_code=400,
                detail="Payment already processed but customer_payment_id missing",
            )

        # Only process PENDING or PROCESSING intents
        if intent.status not in [PaymentIntentStatus.PENDING, PaymentIntentStatus.PROCESSING]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot process payment with status '{intent.status.value}'",
            )

        # Update status to PROCESSING
        intent.status = PaymentIntentStatus.PROCESSING
        self.db.flush()

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
            CustomerPaymentService,
            CustomerPaymentInput,
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

            # Post the payment to clear it
            # Note: This requires a bank account. For Paystack payments, we might
            # need a dedicated "Paystack Settlement" bank account configured.
            # For now, we'll leave it in PENDING status and let it be posted
            # when the settlement is reconciled.
            # TODO: Auto-post when Paystack settlement account is configured

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
        gateway_response: Optional[dict[str, Any]] = None,
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

    def get_intent_by_reference(self, reference: str) -> Optional[PaymentIntent]:
        """Get a payment intent by Paystack reference."""
        return (
            self.db.query(PaymentIntent)
            .filter(PaymentIntent.paystack_reference == reference)
            .first()
        )

    def get_intent_by_id(self, intent_id: UUID) -> Optional[PaymentIntent]:
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
        metadata: Optional[dict[str, Any]] = None,
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

        # Verify transfers are enabled
        transfers_enabled = resolve_value(
            self.db, SettingDomain.payments, "paystack_transfers_enabled"
        )
        if not transfers_enabled:
            raise HTTPException(
                status_code=400,
                detail="Paystack transfers are not enabled",
            )

        # Get expense claim
        claim = self.db.get(ExpenseClaim, claim_id)
        if not claim:
            raise HTTPException(status_code=404, detail=f"Expense claim {expense_claim_id} not found")
        if claim.organization_id != self.organization_id:
            raise HTTPException(status_code=404, detail="Expense claim not found")

        # Validate claim is approved and ready for payment
        if claim.status != ExpenseClaimStatus.APPROVED:
            raise HTTPException(
                status_code=400,
                detail=f"Expense claim with status '{claim.status.value}' cannot be paid",
            )

        if claim.net_payable_amount <= Decimal("0"):
            raise HTTPException(status_code=400, detail="No amount payable for this claim")

        # Get employee for recipient details
        from app.models.people.employee import Employee

        employee = self.db.get(Employee, claim.employee_id)
        if not employee:
            raise HTTPException(status_code=400, detail="Employee not found for expense claim")

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
                logger.warning(f"Invalid transfer bank account ID: {transfer_bank_account_id}")

        # Generate unique reference
        short_uuid = uuid4().hex[:8]
        reference = f"EXP-{claim.claim_number}-{short_uuid}"

        # Amount in kobo (Naira * 100)
        amount_kobo = int(claim.net_payable_amount * 100)

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
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )

        self.db.add(intent)
        self.db.flush()

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

        # Amount in kobo
        amount_kobo = int(intent.amount * 100)

        # Initiate the transfer
        with PaystackClient(paystack_config) as client:
            result = client.initiate_transfer(
                amount=amount_kobo,
                recipient_code=intent.transfer_recipient_code,
                reference=intent.paystack_reference,
                reason=f"Expense reimbursement: {intent.intent_metadata.get('claim_number', '')}",
                currency=intent.currency_code,
            )

        # Update intent with transfer code
        intent.transfer_code = result.transfer_code
        intent.status = PaymentIntentStatus.PROCESSING
        self.db.flush()

        logger.info(
            f"Initiated transfer {result.transfer_code} for intent {intent.intent_id}",
            extra={
                "intent_id": str(intent.intent_id),
                "transfer_code": result.transfer_code,
                "amount": str(intent.amount),
            },
        )

        return intent

    def process_successful_transfer(
        self,
        intent: PaymentIntent,
        completed_at: datetime,
        gateway_response: dict[str, Any],
    ) -> None:
        """
        Process a successful transfer (expense reimbursement).

        Updates the expense claim status to PAID.

        Args:
            intent: The payment intent
            completed_at: When transfer completed
            gateway_response: Full Paystack response
        """
        from app.models.expense.expense_claim import ExpenseClaim, ExpenseClaimStatus

        # Check if already processed
        if intent.status == PaymentIntentStatus.COMPLETED:
            logger.info(f"Transfer intent {intent.intent_id} already completed")
            return

        # Only process PROCESSING intents
        if intent.status != PaymentIntentStatus.PROCESSING:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot complete transfer with status '{intent.status.value}'",
            )

        # Update expense claim status
        if intent.source_type == "EXPENSE_CLAIM" and intent.source_id:
            claim = self.db.get(ExpenseClaim, intent.source_id)
            if claim:
                claim.status = ExpenseClaimStatus.PAID
                claim.paid_on = completed_at.date()
                claim.payment_reference = intent.paystack_reference

        # Update intent
        intent.status = PaymentIntentStatus.COMPLETED
        intent.paid_at = completed_at
        intent.gateway_response = gateway_response

        self.db.flush()

        logger.info(
            f"Processed successful transfer for intent {intent.intent_id}",
            extra={
                "intent_id": str(intent.intent_id),
                "source_type": intent.source_type,
                "source_id": str(intent.source_id) if intent.source_id else None,
            },
        )

    def mark_transfer_failed(
        self,
        intent: PaymentIntent,
        error_message: str,
        gateway_response: Optional[dict[str, Any]] = None,
    ) -> None:
        """
        Mark a transfer intent as failed.

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
            f"Transfer intent {intent.intent_id} failed: {error_message}",
            extra={
                "intent_id": str(intent.intent_id),
                "error": error_message,
            },
        )
