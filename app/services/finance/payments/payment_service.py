"""
Payment Service.

Handles payment intent creation and processing for Paystack integration.
"""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional, cast
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
from app.models.finance.payments.transfer_batch import (
    TransferBatchItem,
    TransferBatchItemStatus,
    TransferBatchStatus,
)
from app.models.domain_settings import SettingDomain
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

    @staticmethod
    def get_intent_by_reference(
        db: Session,
        reference: str,
        organization_id: Optional[UUID] = None,
    ) -> Optional[PaymentIntent]:
        """Get a payment intent by reference (optionally scoped to org)."""
        query = db.query(PaymentIntent).filter(PaymentIntent.paystack_reference == reference)
        if organization_id is not None:
            query = query.filter(PaymentIntent.organization_id == coerce_uuid(organization_id))
        return query.first()

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
                if result.paid_at:
                    try:
                        paid_at = datetime.fromisoformat(result.paid_at.replace("Z", "+00:00"))
                    except ValueError:
                        paid_at = datetime.now(timezone.utc)
                else:
                    paid_at = datetime.now(timezone.utc)

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

        return intent

    def list_pending_transfers(self) -> list[PaymentIntent]:
        """List pending outbound transfers for the organization."""
        return (
            self.db.query(PaymentIntent)
            .filter(
                PaymentIntent.organization_id == self.organization_id,
                PaymentIntent.direction == PaymentDirection.OUTBOUND,
                PaymentIntent.status.in_([
                    PaymentIntentStatus.PENDING,
                    PaymentIntentStatus.PROCESSING,
                ]),
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
                        extra={"payment_id": str(payment.payment_id), "error": str(post_error)},
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

        if claim.net_payable_amount is None or claim.net_payable_amount <= Decimal("0"):
            raise HTTPException(status_code=400, detail="No amount payable for this claim")

        # Get employee for recipient details
        from app.models.people.hr.employee import Employee

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
                reason=f"Expense reimbursement: {(intent.intent_metadata or {}).get('claim_number', '')}",
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
        fee_kobo: Optional[int] = None,
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
        claim = None
        if intent.source_type == "EXPENSE_CLAIM" and intent.source_id:
            claim = self.db.get(ExpenseClaim, intent.source_id)
            if claim:
                claim.status = ExpenseClaimStatus.PAID
                claim.paid_on = completed_at.date()
                claim.payment_reference = intent.paystack_reference

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
                from app.services.expense.expense_posting_adapter import ExpensePostingAdapter

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
                                "journal_entry_id": str(posting_result.journal_entry_id),
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
                system_user_id=system_user_id or (claim.created_by_id if claim else None),
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
        system_user_id: Optional[UUID],
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
                f"Transfer fee not posted - no fee account configured",
                extra={"intent_id": str(intent.intent_id), "fee": str(fee_amount)},
            )
            return

        if not system_user_id:
            logger.warning(
                f"Transfer fee not posted - no user ID available",
                extra={"intent_id": str(intent.intent_id)},
            )
            return

        try:
            from app.services.expense.expense_posting_adapter import ExpensePostingAdapter

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
                    f"Posted transfer fee to GL",
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
        completed_at: Optional[datetime] = None,
        fee_amount: Optional[Decimal] = None,
        error_message: Optional[str] = None,
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
        gateway_response: Optional[dict[str, Any]] = None,
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
                completed_at=datetime.now(timezone.utc),
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
                reversed_at=datetime.now(timezone.utc),
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
        reason: Optional[str] = None,
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
        if intent.status not in [PaymentIntentStatus.COMPLETED, PaymentIntentStatus.PROCESSING]:
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
            logger.warning(f"Cannot post reversal entries - no user ID")
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
