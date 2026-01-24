"""
Payment API Routes.

Handles payment initialization, verification, and webhooks for Paystack integration.
"""
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.db import SessionLocal
from app.models.domain_settings import SettingDomain
from app.services.finance.payments import (
    PaymentService,
    PaystackConfig,
    PaystackError,
    WebhookService,
)
from app.services.settings_spec import resolve_value

logger = logging.getLogger(__name__)

# Main router for authenticated endpoints
router = APIRouter(prefix="/payments", tags=["payments"])

# Separate router for webhook (no authentication - uses signature verification)
webhook_router = APIRouter(prefix="/payments", tags=["payments-webhook"])


def get_db():
    """Get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =============================================================================
# Pydantic Schemas
# =============================================================================


class InitializeInvoicePaymentRequest(BaseModel):
    """Request to initialize a payment for an invoice."""

    invoice_id: UUID


class InitializePaymentResponse(BaseModel):
    """Response from payment initialization."""

    intent_id: UUID
    authorization_url: str
    reference: str
    amount: float
    currency: str


class PaymentStatusResponse(BaseModel):
    """Response with payment status."""

    intent_id: UUID
    status: str
    amount: float
    currency: str
    paid_at: Optional[str] = None
    invoice_number: Optional[str] = None
    customer_payment_id: Optional[UUID] = None


class WebhookResponse(BaseModel):
    """Response to webhook."""

    status: str
    message: Optional[str] = None


# -----------------------------------------------------------------------------
# Expense Transfer Schemas
# -----------------------------------------------------------------------------


class BankInfo(BaseModel):
    """Bank information."""

    code: str
    name: str


class ResolveAccountRequest(BaseModel):
    """Request to resolve a bank account."""

    bank_code: str = Field(..., description="Bank code (e.g., '058' for GTBank)")
    account_number: str = Field(..., min_length=10, max_length=10)


class ResolveAccountResponse(BaseModel):
    """Response from account resolution."""

    account_number: str
    account_name: str
    bank_code: str


class InitializeExpensePaymentRequest(BaseModel):
    """Request to initialize expense reimbursement."""

    expense_claim_id: UUID
    bank_code: str = Field(..., description="Recipient's bank code")
    account_number: str = Field(..., min_length=10, max_length=10)


class ExpensePaymentResponse(BaseModel):
    """Response for expense payment intent."""

    intent_id: UUID
    reference: str
    amount: float
    currency: str
    status: str
    recipient_account_name: Optional[str] = None
    recipient_bank_code: Optional[str] = None
    recipient_account_number: Optional[str] = None


class InitiateTransferResponse(BaseModel):
    """Response from transfer initiation."""

    intent_id: UUID
    transfer_code: str
    status: str
    amount: float
    currency: str


# =============================================================================
# Helpers
# =============================================================================


def get_paystack_config(db: Session, organization_id: UUID) -> PaystackConfig:
    """
    Get Paystack configuration for organization.

    Raises HTTPException if Paystack is not enabled or configured.
    """
    # Check if enabled
    enabled = resolve_value(db, SettingDomain.payments, "paystack_enabled")
    if not enabled:
        raise HTTPException(
            status_code=400,
            detail="Paystack payment integration is not enabled for this organization",
        )

    # Get keys
    secret_key = resolve_value(db, SettingDomain.payments, "paystack_secret_key")
    public_key = resolve_value(db, SettingDomain.payments, "paystack_public_key")
    webhook_secret = resolve_value(db, SettingDomain.payments, "paystack_webhook_secret")

    if not secret_key:
        raise HTTPException(
            status_code=500,
            detail="Paystack secret key not configured",
        )

    if not public_key:
        raise HTTPException(
            status_code=500,
            detail="Paystack public key not configured",
        )

    return PaystackConfig(
        secret_key=str(secret_key),
        public_key=str(public_key),
        webhook_secret=str(webhook_secret or ""),
    )


# =============================================================================
# Endpoints
# =============================================================================


@router.post("/initialize/invoice", response_model=InitializePaymentResponse)
def initialize_invoice_payment(
    request_data: InitializeInvoicePaymentRequest,
    request: Request,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """
    Initialize a Paystack payment for an invoice.

    Creates a payment intent and returns the Paystack authorization URL
    to redirect the customer for payment.
    """
    config = get_paystack_config(db, organization_id)

    # Build callback URL
    # Check for configured base URL first, then fall back to request base
    callback_base = resolve_value(db, SettingDomain.payments, "paystack_callback_base_url")
    if callback_base:
        base_url = str(callback_base).rstrip("/")
    else:
        base_url = str(request.base_url).rstrip("/")

    callback_url = f"{base_url}/finance/payments/callback"

    svc = PaymentService(db, organization_id)
    try:
        intent = svc.create_invoice_payment_intent(
            invoice_id=request_data.invoice_id,
            callback_url=callback_url,
            paystack_config=config,
        )
        db.commit()
    except PaystackError as e:
        logger.error(f"Paystack initialization failed: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Payment gateway error: {e.message}",
        )

    return InitializePaymentResponse(
        intent_id=intent.intent_id,
        authorization_url=intent.authorization_url or "",
        reference=intent.paystack_reference,
        amount=float(intent.amount),
        currency=intent.currency_code,
    )


@router.get("/status/{reference}", response_model=PaymentStatusResponse)
def get_payment_status(
    reference: str,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """
    Get payment status by reference.

    Returns the current status of a payment intent.
    """
    from app.models.finance.payments import PaymentIntent

    intent = (
        db.query(PaymentIntent)
        .filter(
            PaymentIntent.paystack_reference == reference,
            PaymentIntent.organization_id == organization_id,
        )
        .first()
    )

    if not intent:
        raise HTTPException(status_code=404, detail="Payment not found")

    return PaymentStatusResponse(
        intent_id=intent.intent_id,
        status=intent.status.value,
        amount=float(intent.amount),
        currency=intent.currency_code,
        paid_at=intent.paid_at.isoformat() if intent.paid_at else None,
        invoice_number=intent.intent_metadata.get("invoice_number") if intent.intent_metadata else None,
        customer_payment_id=intent.customer_payment_id,
    )


@router.get("/intent/{intent_id}", response_model=PaymentStatusResponse)
def get_payment_intent(
    intent_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """
    Get payment intent by ID.

    Returns the current status of a payment intent.
    """
    svc = PaymentService(db, organization_id)
    intent = svc.get_intent_by_id(intent_id)

    if not intent:
        raise HTTPException(status_code=404, detail="Payment intent not found")

    return PaymentStatusResponse(
        intent_id=intent.intent_id,
        status=intent.status.value,
        amount=float(intent.amount),
        currency=intent.currency_code,
        paid_at=intent.paid_at.isoformat() if intent.paid_at else None,
        invoice_number=intent.intent_metadata.get("invoice_number") if intent.intent_metadata else None,
        customer_payment_id=intent.customer_payment_id,
    )


@router.post("/verify/{reference}", response_model=PaymentStatusResponse)
def verify_payment(
    reference: str,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """
    Verify a payment with Paystack.

    Queries Paystack to get the current status of a payment and updates
    the local payment intent accordingly. Use this if webhook was missed.
    """
    from app.models.finance.payments import PaymentIntent, PaymentIntentStatus
    from datetime import datetime, timezone

    intent = (
        db.query(PaymentIntent)
        .filter(
            PaymentIntent.paystack_reference == reference,
            PaymentIntent.organization_id == organization_id,
        )
        .first()
    )

    if not intent:
        raise HTTPException(status_code=404, detail="Payment not found")

    # If already completed, just return status
    if intent.status == PaymentIntentStatus.COMPLETED:
        return PaymentStatusResponse(
            intent_id=intent.intent_id,
            status=intent.status.value,
            amount=float(intent.amount),
            currency=intent.currency_code,
            paid_at=intent.paid_at.isoformat() if intent.paid_at else None,
            invoice_number=intent.intent_metadata.get("invoice_number") if intent.intent_metadata else None,
            customer_payment_id=intent.customer_payment_id,
        )

    # Verify with Paystack
    config = get_paystack_config(db, organization_id)
    svc = PaymentService(db, organization_id)

    from app.services.finance.payments import PaystackClient

    try:
        with PaystackClient(config) as client:
            result = client.verify_transaction(reference)

        if result.status == "success":
            # Parse paid_at
            if result.paid_at:
                try:
                    paid_at = datetime.fromisoformat(result.paid_at.replace("Z", "+00:00"))
                except ValueError:
                    paid_at = datetime.now(timezone.utc)
            else:
                paid_at = datetime.now(timezone.utc)

            # Process the successful payment
            svc.process_successful_payment(
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
            db.commit()

        elif result.status == "failed":
            svc.mark_payment_failed(
                intent,
                result.gateway_response or "Payment failed",
            )
            db.commit()

        elif result.status == "abandoned":
            svc.mark_payment_abandoned(intent)
            db.commit()

    except PaystackError as e:
        logger.error(f"Paystack verification failed: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Payment verification failed: {e.message}",
        )

    # Refresh intent
    db.refresh(intent)

    return PaymentStatusResponse(
        intent_id=intent.intent_id,
        status=intent.status.value,
        amount=float(intent.amount),
        currency=intent.currency_code,
        paid_at=intent.paid_at.isoformat() if intent.paid_at else None,
        invoice_number=intent.intent_metadata.get("invoice_number") if intent.intent_metadata else None,
        customer_payment_id=intent.customer_payment_id,
    )


# =============================================================================
# Expense Reimbursement (Transfer) Endpoints
# =============================================================================


@router.get("/banks", response_model=list[BankInfo])
def list_banks(
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """
    List supported banks for transfers.

    Returns list of Nigerian banks supported by Paystack.
    """
    from app.services.finance.payments import PaystackClient

    config = get_paystack_config(db, organization_id)

    try:
        with PaystackClient(config) as client:
            banks = client.list_banks(country="nigeria")

        return [BankInfo(code=b.code, name=b.name) for b in banks]

    except PaystackError as e:
        logger.error(f"Failed to list banks: {e}")
        raise HTTPException(status_code=502, detail=f"Payment gateway error: {e.message}")


@router.post("/resolve-account", response_model=ResolveAccountResponse)
def resolve_bank_account(
    request_data: ResolveAccountRequest,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """
    Resolve a bank account to verify it exists and get the account name.

    Use this before initiating expense reimbursement to confirm bank details.
    """
    from app.services.finance.payments import PaystackClient

    config = get_paystack_config(db, organization_id)

    try:
        with PaystackClient(config) as client:
            result = client.resolve_account(
                account_number=request_data.account_number,
                bank_code=request_data.bank_code,
            )

        return ResolveAccountResponse(
            account_number=result.account_number,
            account_name=result.account_name,
            bank_code=request_data.bank_code,
        )

    except PaystackError as e:
        logger.error(f"Account resolution failed: {e}")
        if "Could not resolve account name" in str(e.message):
            raise HTTPException(
                status_code=400,
                detail="Invalid account number or bank code",
            )
        raise HTTPException(status_code=502, detail=f"Payment gateway error: {e.message}")


@router.post("/initialize/expense", response_model=ExpensePaymentResponse)
def initialize_expense_payment(
    request_data: InitializeExpensePaymentRequest,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """
    Initialize an expense reimbursement payment (transfer).

    Creates a payment intent for an expense claim. This does NOT initiate
    the transfer yet - call /transfers/{intent_id}/initiate to execute.

    Requires:
    - Paystack transfers must be enabled
    - Expense claim must be approved
    - Bank account details must be valid
    """
    # Check if transfers are enabled
    transfers_enabled = resolve_value(db, SettingDomain.payments, "paystack_transfers_enabled")
    if not transfers_enabled:
        raise HTTPException(
            status_code=400,
            detail="Paystack transfers are not enabled. Contact administrator.",
        )

    config = get_paystack_config(db, organization_id)

    svc = PaymentService(db, organization_id)
    try:
        intent = svc.create_expense_payment_intent(
            expense_claim_id=request_data.expense_claim_id,
            paystack_config=config,
            recipient_bank_code=request_data.bank_code,
            recipient_account_number=request_data.account_number,
        )
        db.commit()

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PaystackError as e:
        logger.error(f"Expense payment initialization failed: {e}")
        raise HTTPException(status_code=502, detail=f"Payment gateway error: {e.message}")

    return ExpensePaymentResponse(
        intent_id=intent.intent_id,
        reference=intent.paystack_reference,
        amount=float(intent.amount),
        currency=intent.currency_code,
        status=intent.status.value,
        recipient_account_name=intent.recipient_account_name,
        recipient_bank_code=intent.recipient_bank_code,
        recipient_account_number=intent.recipient_account_number,
    )


@router.post("/transfers/{intent_id}/initiate", response_model=InitiateTransferResponse)
def initiate_transfer(
    intent_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """
    Initiate a Paystack transfer for an expense reimbursement.

    The payment intent must have been created with /initialize/expense first.
    This actually sends the money to the recipient's bank account.

    Authorization: Requires appropriate permission to process payments.
    """
    from app.models.finance.payments import PaymentIntentStatus

    # Check if transfers are enabled
    transfers_enabled = resolve_value(db, SettingDomain.payments, "paystack_transfers_enabled")
    if not transfers_enabled:
        raise HTTPException(
            status_code=400,
            detail="Paystack transfers are not enabled",
        )

    config = get_paystack_config(db, organization_id)
    svc = PaymentService(db, organization_id)

    intent = svc.get_intent_by_id(intent_id)
    if not intent:
        raise HTTPException(status_code=404, detail="Payment intent not found")

    if intent.status != PaymentIntentStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot initiate transfer: intent status is {intent.status.value}",
        )

    if intent.direction.value != "OUTBOUND":
        raise HTTPException(
            status_code=400,
            detail="This payment intent is not an outbound transfer",
        )

    try:
        updated_intent = svc.initiate_expense_transfer(
            intent=intent,
            paystack_config=config,
        )
        db.commit()

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PaystackError as e:
        logger.error(f"Transfer initiation failed: {e}")
        raise HTTPException(status_code=502, detail=f"Transfer failed: {e.message}")

    return InitiateTransferResponse(
        intent_id=updated_intent.intent_id,
        transfer_code=updated_intent.transfer_code or "",
        status=updated_intent.status.value,
        amount=float(updated_intent.amount),
        currency=updated_intent.currency_code,
    )


@router.get("/transfers/pending")
def list_pending_transfers(
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """
    List pending expense reimbursement transfers.

    Returns transfers that have been initialized but not yet completed.
    """
    from app.models.finance.payments import PaymentIntent, PaymentIntentStatus, PaymentDirection

    intents = (
        db.query(PaymentIntent)
        .filter(
            PaymentIntent.organization_id == organization_id,
            PaymentIntent.direction == PaymentDirection.OUTBOUND,
            PaymentIntent.status.in_([
                PaymentIntentStatus.PENDING,
                PaymentIntentStatus.PROCESSING,
            ]),
        )
        .order_by(PaymentIntent.created_at.desc())
        .all()
    )

    return [
        ExpensePaymentResponse(
            intent_id=i.intent_id,
            reference=i.paystack_reference,
            amount=float(i.amount),
            currency=i.currency_code,
            status=i.status.value,
            recipient_account_name=i.recipient_account_name,
            recipient_bank_code=i.recipient_bank_code,
            recipient_account_number=i.recipient_account_number,
        )
        for i in intents
    ]


# =============================================================================
# Webhook Endpoint (No Authentication - Uses Signature Verification)
# =============================================================================


@webhook_router.post("/webhook/paystack", response_model=WebhookResponse)
async def paystack_webhook(
    request: Request,
    x_paystack_signature: str = Header(None, alias="X-Paystack-Signature"),
    db: Session = Depends(get_db),
):
    """
    Handle Paystack webhook events.

    This endpoint does NOT require authentication - it uses Paystack's
    signature verification instead.

    Paystack will send webhooks for events like:
    - charge.success: Payment was successful
    - charge.failed: Payment failed
    - transfer.success: Transfer completed
    - transfer.failed: Transfer failed
    """
    if not x_paystack_signature:
        logger.warning("Webhook received without signature")
        raise HTTPException(status_code=400, detail="Missing signature")

    raw_body = await request.body()

    try:
        payload = await request.json()
    except Exception as e:
        logger.error(f"Invalid webhook payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event_type = payload.get("event")
    event_data = payload.get("data", {})

    if not event_type:
        logger.warning("Webhook received without event type")
        raise HTTPException(status_code=400, detail="Missing event type")

    # Get reference to find organization and config
    reference = event_data.get("reference", "")

    from app.models.finance.payments import PaymentIntent

    intent = (
        db.query(PaymentIntent)
        .filter(PaymentIntent.paystack_reference == reference)
        .first()
    )

    if not intent:
        # Log but don't fail - might be test webhook or unknown reference
        logger.warning(f"Webhook for unknown reference: {reference}")
        return WebhookResponse(
            status="ignored",
            message=f"Unknown reference: {reference}",
        )

    # Get Paystack config for this organization
    try:
        config = get_paystack_config(db, intent.organization_id)
    except HTTPException as e:
        logger.error(f"Failed to get Paystack config: {e.detail}")
        return WebhookResponse(
            status="error",
            message="Paystack not configured for organization",
        )

    # Process webhook
    svc = WebhookService(db)
    try:
        webhook = svc.process_webhook(
            event_type=event_type,
            event_data=event_data,
            paystack_config=config,
            raw_payload=raw_body,
            signature=x_paystack_signature,
        )
        db.commit()

        return WebhookResponse(
            status=webhook.status.value,
            message=webhook.error_message,
        )

    except ValueError as e:
        # Signature verification failed
        logger.warning(f"Webhook signature verification failed: {e}")
        raise HTTPException(status_code=401, detail=str(e))

    except Exception as e:
        logger.exception(f"Webhook processing error: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Webhook processing failed")
