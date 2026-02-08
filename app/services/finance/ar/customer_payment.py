"""
CustomerPaymentService - AR payment receipt processing.

Manages customer payment creation, posting, and allocation to invoices.
"""

from __future__ import annotations

import logging
import uuid as uuid_lib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.finance.ar.customer import Customer
from app.models.finance.ar.customer_payment import (
    CustomerPayment,
    PaymentMethod,
    PaymentStatus,
)
from app.models.finance.ar.invoice import Invoice, InvoiceStatus
from app.models.finance.ar.payment_allocation import PaymentAllocation
from app.models.finance.core_config.numbering_sequence import SequenceType
from app.services.common import coerce_uuid
from app.services.finance.ar.input_utils import (
    parse_date_str,
    parse_decimal,
    parse_json_list,
    require_uuid,
    resolve_currency_code,
)
from app.services.finance.platform.sequence import SequenceService
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class PaymentAllocationInput:
    """Input for allocating payment to an invoice."""

    invoice_id: UUID
    amount: Decimal


@dataclass
class CustomerPaymentInput:
    """Input for creating a customer payment."""

    customer_id: UUID
    payment_date: date
    payment_method: PaymentMethod
    currency_code: str
    amount: Decimal  # Net amount received (after WHT)
    bank_account_id: UUID | None = None
    allocations: list[PaymentAllocationInput] = field(default_factory=list)
    exchange_rate: Decimal | None = None
    reference: str | None = None
    description: str | None = None
    correlation_id: str | None = None
    # Withholding Tax (WHT) - when customer deducts WHT before paying
    gross_amount: Decimal | None = None  # If not provided, defaults to amount (no WHT)
    wht_code_id: UUID | None = None  # WHT tax code applied
    wht_amount: Decimal = field(default_factory=lambda: Decimal("0"))  # WHT deducted
    wht_certificate_number: str | None = None  # Certificate received from customer


class CustomerPaymentService(ListResponseMixin):
    """
    Service for customer payment receipt processing.

    Manages payment creation, posting, and invoice allocation.
    """

    @staticmethod
    def create_payment(
        db: Session,
        organization_id: UUID,
        input: CustomerPaymentInput,
        created_by_user_id: UUID,
    ) -> CustomerPayment:
        """
        Create a new customer payment receipt.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Payment input data
            created_by_user_id: User creating the payment

        Returns:
            Created CustomerPayment
        """
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(created_by_user_id)
        customer_id = coerce_uuid(input.customer_id)

        # Validate customer
        customer = db.get(Customer, customer_id)
        if not customer or customer.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Customer not found")

        if not customer.is_active:
            raise HTTPException(status_code=400, detail="Customer is not active")

        # Validate allocations
        if input.allocations:
            allocation_total = sum(a.amount for a in input.allocations)
            if allocation_total > input.amount:
                raise HTTPException(
                    status_code=400,
                    detail="Allocation total exceeds payment amount",
                )

            for alloc in input.allocations:
                invoice = db.get(Invoice, coerce_uuid(alloc.invoice_id))
                if not invoice or invoice.organization_id != org_id:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Invoice {alloc.invoice_id} not found",
                    )
                if invoice.customer_id != customer_id:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invoice {invoice.invoice_number} belongs to different customer",
                    )
                payable_statuses = [
                    InvoiceStatus.POSTED,
                    InvoiceStatus.PARTIALLY_PAID,
                    InvoiceStatus.OVERDUE,
                ]
                if invoice.status not in payable_statuses:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invoice {invoice.invoice_number} is not payable",
                    )
                if alloc.amount > invoice.balance_due:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Allocation exceeds balance due on {invoice.invoice_number}",
                    )

        # Handle WHT amounts (validate BEFORE generating sequence number)
        # gross_amount = amount before WHT deduction
        # amount = net amount received (after WHT)
        # wht_amount = WHT deducted by customer
        wht_amount = input.wht_amount or Decimal("0")
        net_amount = input.amount  # The 'amount' input is the net received

        # If gross_amount is provided, use it; otherwise calculate from net + WHT
        if input.gross_amount is not None:
            gross_amount = input.gross_amount
            # Validate: gross = net + wht
            expected_wht = gross_amount - net_amount
            if wht_amount > Decimal("0") and abs(expected_wht - wht_amount) > Decimal(
                "0.01"
            ):
                raise HTTPException(
                    status_code=400,
                    detail=f"WHT amount ({wht_amount}) doesn't match gross - net ({expected_wht})",
                )
            if wht_amount == Decimal("0") and gross_amount != net_amount:
                wht_amount = expected_wht
        else:
            # No gross amount provided - calculate from net + WHT
            gross_amount = net_amount + wht_amount

        # Generate payment number (after all validation passes)
        payment_number = SequenceService.get_next_number(
            db, org_id, SequenceType.RECEIPT
        )

        # If customer has WHT applicable and no WHT provided, warn (but don't block)
        # The user may have a valid reason (exemption, etc.)

        # Calculate functional currency amount
        exchange_rate = input.exchange_rate or Decimal("1.0")
        functional_amount = net_amount * exchange_rate

        # Create payment
        payment = CustomerPayment(
            organization_id=org_id,
            customer_id=customer_id,
            payment_number=payment_number,
            payment_date=input.payment_date,
            payment_method=input.payment_method,
            currency_code=input.currency_code,
            gross_amount=gross_amount,
            amount=net_amount,
            wht_code_id=input.wht_code_id,
            wht_amount=wht_amount,
            wht_certificate_number=input.wht_certificate_number,
            exchange_rate=exchange_rate,
            functional_currency_amount=functional_amount,
            bank_account_id=input.bank_account_id,
            reference=input.reference,
            description=input.description,
            status=PaymentStatus.PENDING,
            created_by_user_id=user_id,
            correlation_id=input.correlation_id or str(uuid_lib.uuid4()),
        )

        db.add(payment)
        db.flush()

        # Create allocations
        for alloc in input.allocations:
            allocation = PaymentAllocation(
                payment_id=payment.payment_id,
                invoice_id=coerce_uuid(alloc.invoice_id),
                allocated_amount=alloc.amount,
            )
            db.add(allocation)

        db.commit()
        db.refresh(payment)

        return payment

    @staticmethod
    def build_input_from_payload(
        db: Session,
        organization_id: UUID,
        payload: dict,
    ) -> CustomerPaymentInput:
        """Build CustomerPaymentInput from raw payload (strings or JSON)."""
        payment_date = (
            parse_date_str(payload.get("payment_date"), "Payment date") or date.today()
        )

        method_str = payload.get("payment_method", "BANK_TRANSFER")
        try:
            payment_method = PaymentMethod(method_str)
        except ValueError:
            payment_method = PaymentMethod.BANK_TRANSFER

        allocations: list[PaymentAllocationInput] = []
        allocations_data = parse_json_list(payload.get("allocations"), "Allocations")
        for alloc in allocations_data:
            if alloc.get("invoice_id") and alloc.get("amount"):
                allocations.append(
                    PaymentAllocationInput(
                        invoice_id=require_uuid(alloc.get("invoice_id"), "Invoice"),
                        amount=parse_decimal(alloc.get("amount"), "Allocation amount"),
                    )
                )

        has_wht = payload.get("has_wht") in ("true", "1", True, "on")
        wht_code_id = coerce_uuid(payload.get("wht_code_id")) if has_wht else None
        wht_amount = (
            parse_decimal(payload.get("wht_amount", "0"), "WHT amount")
            if has_wht
            else Decimal("0")
        )
        gross_amount = (
            parse_decimal(payload.get("gross_amount"), "Gross amount")
            if has_wht and payload.get("gross_amount") is not None
            else None
        )
        wht_certificate_number = (
            payload.get("wht_certificate_number") if has_wht else None
        )

        customer_id = require_uuid(payload.get("customer_id"), "Customer")
        currency_code = resolve_currency_code(
            db, coerce_uuid(organization_id), payload.get("currency_code")
        )

        return CustomerPaymentInput(
            customer_id=customer_id,
            payment_date=payment_date,
            payment_method=payment_method,
            currency_code=currency_code,
            amount=parse_decimal(payload.get("amount", 0), "Amount"),
            bank_account_id=coerce_uuid(payload.get("bank_account_id"))
            if payload.get("bank_account_id")
            else None,
            reference=payload.get("reference"),
            description=payload.get("description"),
            allocations=allocations,
            gross_amount=gross_amount,
            wht_code_id=wht_code_id,
            wht_amount=wht_amount,
            wht_certificate_number=wht_certificate_number,
        )

    @staticmethod
    def post_payment(
        db: Session,
        organization_id: UUID,
        payment_id: UUID,
        posted_by_user_id: UUID,
        posting_date: date | None = None,
    ) -> CustomerPayment:
        """
        Post a payment to the general ledger and apply allocations.

        Args:
            db: Database session
            organization_id: Organization scope
            payment_id: Payment to post
            posted_by_user_id: User posting
            posting_date: Optional posting date

        Returns:
            Updated CustomerPayment
        """

        org_id = coerce_uuid(organization_id)
        pay_id = coerce_uuid(payment_id)
        user_id = coerce_uuid(posted_by_user_id)

        payment = db.get(CustomerPayment, pay_id)
        if not payment or payment.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Payment not found")

        if payment.status != PaymentStatus.PENDING:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot post payment with status '{payment.status.value}'",
            )

        # For AR payments, we need a bank account
        if not payment.bank_account_id:
            raise HTTPException(
                status_code=400,
                detail="Bank account is required to post payment",
            )

        # Temporarily update status for posting adapter check
        # The adapter expects APPROVED but AR model uses PENDING

        # Create journal entry manually since we don't have APPROVED status
        from app.models.finance.gl.journal_entry import JournalType
        from app.services.finance.gl.journal import (
            JournalInput,
            JournalLineInput,
            JournalService,
        )
        from app.services.finance.gl.ledger_posting import (
            LedgerPostingService,
            PostingRequest,
        )

        customer = db.get(Customer, payment.customer_id)
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        exchange_rate = payment.exchange_rate or Decimal("1.0")
        net_amount = payment.amount  # Net amount received
        gross_amount = payment.gross_amount  # Gross amount before WHT
        wht_amount = payment.wht_amount or Decimal("0")

        net_functional = net_amount * exchange_rate
        gross_functional = gross_amount * exchange_rate
        wht_functional = wht_amount * exchange_rate

        journal_lines = [
            # Dr Bank (net amount received)
            JournalLineInput(
                account_id=payment.bank_account_id,
                debit_amount=net_amount,
                credit_amount=Decimal("0"),
                debit_amount_functional=net_functional,
                credit_amount_functional=Decimal("0"),
                description=f"AR Payment: {payment.reference or payment.payment_number}",
            ),
        ]

        # If WHT was deducted, add WHT Receivable entry
        if wht_amount > Decimal("0"):
            # Get WHT Receivable account from tax code or organization settings
            wht_receivable_account_id = None
            if payment.wht_code_id:
                from app.models.finance.tax.tax_code import TaxCode

                wht_code = db.get(TaxCode, payment.wht_code_id)
                if wht_code:
                    # Use the tax_paid_account_id as WHT Receivable
                    wht_receivable_account_id = wht_code.tax_paid_account_id

            if not wht_receivable_account_id:
                raise HTTPException(
                    status_code=400,
                    detail="WHT Receivable account not configured. Please set up the WHT tax code with a Tax Paid Account.",
                )

            journal_lines.append(
                # Dr WHT Receivable (WHT amount deducted by customer)
                JournalLineInput(
                    account_id=wht_receivable_account_id,
                    debit_amount=wht_amount,
                    credit_amount=Decimal("0"),
                    debit_amount_functional=wht_functional,
                    credit_amount_functional=Decimal("0"),
                    description=f"WHT deducted by {customer.legal_name} (Cert: {payment.wht_certificate_number or 'N/A'})",
                )
            )

        # Cr AR Control (gross amount - the full invoice amount being settled)
        journal_lines.append(
            JournalLineInput(
                account_id=customer.ar_control_account_id,
                debit_amount=Decimal("0"),
                credit_amount=gross_amount,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=gross_functional,
                description=f"Payment from {customer.legal_name}",
            )
        )

        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=payment.payment_date,
            posting_date=posting_date or payment.payment_date,
            description=f"AR Payment {payment.payment_number} - {customer.legal_name}",
            reference=payment.reference or payment.payment_number,
            currency_code=payment.currency_code,
            exchange_rate=exchange_rate,
            lines=journal_lines,
            source_module="AR",
            source_document_type="CUSTOMER_PAYMENT",
            source_document_id=pay_id,
            correlation_id=payment.correlation_id,
        )

        try:
            journal = JournalService.create_journal(db, org_id, journal_input, user_id)
            JournalService.submit_journal(db, org_id, journal.journal_entry_id, user_id)
            JournalService.approve_journal(
                db, org_id, journal.journal_entry_id, user_id
            )
        except HTTPException as e:
            raise HTTPException(
                status_code=400,
                detail=f"Journal creation failed: {e.detail}",
            )

        # Post to ledger
        idempotency_key = f"{org_id}:AR:PAY:{pay_id}:post:v1"

        posting_request = PostingRequest(
            organization_id=org_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date or payment.payment_date,
            idempotency_key=idempotency_key,
            source_module="AR",
            correlation_id=payment.correlation_id,
            posted_by_user_id=user_id,
        )

        posting_result = LedgerPostingService.post_journal_entry(db, posting_request)

        if not posting_result.success:
            raise HTTPException(
                status_code=400,
                detail=f"Ledger posting failed: {posting_result.message}",
            )

        # Update payment status
        payment.status = PaymentStatus.CLEARED
        payment.posted_by_user_id = user_id
        payment.posted_at = datetime.now(UTC)
        payment.journal_entry_id = journal.journal_entry_id
        payment.posting_batch_id = posting_result.posting_batch_id

        # Create tax transaction for WHT if applicable
        if wht_amount > Decimal("0") and payment.wht_code_id:
            from app.models.finance.gl.fiscal_period import FiscalPeriod
            from app.models.finance.tax.tax_code import TaxCode
            from app.models.finance.tax.tax_transaction import TaxTransactionType
            from app.services.finance.tax.tax_transaction import (
                TaxTransactionInput,
                tax_transaction_service,
            )

            fiscal_period = (
                db.query(FiscalPeriod)
                .filter(
                    FiscalPeriod.organization_id == org_id,
                    FiscalPeriod.start_date <= payment.payment_date,
                    FiscalPeriod.end_date >= payment.payment_date,
                )
                .first()
            )

            tax_code = db.get(TaxCode, payment.wht_code_id)
            if fiscal_period and tax_code and tax_code.organization_id == org_id:
                tax_transaction_service.create_transaction(
                    db=db,
                    organization_id=org_id,
                    input=TaxTransactionInput(
                        fiscal_period_id=fiscal_period.fiscal_period_id,
                        tax_code_id=payment.wht_code_id,
                        jurisdiction_id=tax_code.jurisdiction_id,
                        transaction_type=TaxTransactionType.WITHHOLDING,
                        transaction_date=payment.payment_date,
                        source_document_type="CUSTOMER_PAYMENT",
                        source_document_id=pay_id,
                        source_document_reference=payment.payment_number,
                        currency_code=payment.currency_code,
                        base_amount=gross_amount,  # WHT calculated on gross
                        tax_rate=tax_code.tax_rate,
                        tax_amount=wht_amount,
                        functional_base_amount=gross_amount * exchange_rate,
                        functional_tax_amount=wht_amount * exchange_rate,
                        exchange_rate=exchange_rate,
                        counterparty_name=customer.legal_name,
                        counterparty_tax_id=customer.tax_identification_number,
                    ),
                )

        # Apply allocations to invoices
        allocations = (
            db.query(PaymentAllocation)
            .filter(PaymentAllocation.payment_id == pay_id)
            .all()
        )

        for alloc in allocations:
            invoice = db.get(Invoice, alloc.invoice_id)
            if invoice:
                invoice.amount_paid += alloc.allocated_amount
                if invoice.amount_paid >= invoice.total_amount:
                    invoice.status = InvoiceStatus.PAID
                else:
                    invoice.status = InvoiceStatus.PARTIALLY_PAID

        db.commit()
        db.refresh(payment)

        return payment

    @staticmethod
    def void_payment(
        db: Session,
        organization_id: UUID,
        payment_id: UUID,
        voided_by_user_id: UUID,
        reason: str,
    ) -> CustomerPayment:
        """Void a payment."""
        org_id = coerce_uuid(organization_id)
        pay_id = coerce_uuid(payment_id)

        payment = db.get(CustomerPayment, pay_id)
        if not payment or payment.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Payment not found")

        if payment.status == PaymentStatus.VOID:
            raise HTTPException(status_code=400, detail="Payment is already voided")

        # Reverse allocations if payment was cleared
        if payment.status == PaymentStatus.CLEARED:
            allocations = (
                db.query(PaymentAllocation)
                .filter(PaymentAllocation.payment_id == pay_id)
                .all()
            )

            for alloc in allocations:
                invoice = db.get(Invoice, alloc.invoice_id)
                if invoice:
                    invoice.amount_paid -= alloc.allocated_amount
                    if invoice.amount_paid <= Decimal("0"):
                        invoice.status = InvoiceStatus.POSTED
                    else:
                        invoice.status = InvoiceStatus.PARTIALLY_PAID

        payment.status = PaymentStatus.VOID

        db.commit()
        db.refresh(payment)

        return payment

    @staticmethod
    def mark_bounced(
        db: Session,
        organization_id: UUID,
        payment_id: UUID,
        reason: str,
    ) -> CustomerPayment:
        """Mark a payment as bounced."""
        org_id = coerce_uuid(organization_id)
        pay_id = coerce_uuid(payment_id)

        payment = db.get(CustomerPayment, pay_id)
        if not payment or payment.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Payment not found")

        if payment.status not in [PaymentStatus.PENDING, PaymentStatus.CLEARED]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot mark payment as bounced with status '{payment.status.value}'",
            )

        # Reverse allocations if payment was cleared
        if payment.status == PaymentStatus.CLEARED:
            allocations = (
                db.query(PaymentAllocation)
                .filter(PaymentAllocation.payment_id == pay_id)
                .all()
            )

            for alloc in allocations:
                invoice = db.get(Invoice, alloc.invoice_id)
                if invoice:
                    invoice.amount_paid -= alloc.allocated_amount
                    if invoice.amount_paid <= Decimal("0"):
                        invoice.status = InvoiceStatus.POSTED
                    else:
                        invoice.status = InvoiceStatus.PARTIALLY_PAID

        payment.status = PaymentStatus.BOUNCED

        db.commit()
        db.refresh(payment)

        return payment

    @staticmethod
    def update_payment(
        db: Session,
        organization_id: UUID,
        payment_id: UUID,
        input: CustomerPaymentInput,
        updated_by_user_id: UUID,
    ) -> CustomerPayment:
        """
        Update an existing customer payment receipt.

        Only PENDING payments can be updated.

        Args:
            db: Database session
            organization_id: Organization scope
            payment_id: Payment to update
            input: Updated payment data
            updated_by_user_id: User making the update

        Returns:
            Updated CustomerPayment
        """
        org_id = coerce_uuid(organization_id)
        pay_id = coerce_uuid(payment_id)
        coerce_uuid(updated_by_user_id)
        customer_id = coerce_uuid(input.customer_id)

        payment = db.get(CustomerPayment, pay_id)
        if not payment or payment.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Payment not found")

        if payment.status != PaymentStatus.PENDING:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot edit payment with status '{payment.status.value}'. Only PENDING payments can be edited.",
            )

        # Validate customer
        customer = db.get(Customer, customer_id)
        if not customer or customer.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Customer not found")

        if not customer.is_active:
            raise HTTPException(status_code=400, detail="Customer is not active")

        # Validate allocations
        if input.allocations:
            allocation_total = sum(a.amount for a in input.allocations)
            if allocation_total > input.amount:
                raise HTTPException(
                    status_code=400,
                    detail="Allocation total exceeds payment amount",
                )

            for alloc in input.allocations:
                invoice = db.get(Invoice, coerce_uuid(alloc.invoice_id))
                if not invoice or invoice.organization_id != org_id:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Invoice {alloc.invoice_id} not found",
                    )
                if invoice.customer_id != customer_id:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invoice {invoice.invoice_number} belongs to different customer",
                    )
                payable_statuses = [
                    InvoiceStatus.POSTED,
                    InvoiceStatus.PARTIALLY_PAID,
                    InvoiceStatus.OVERDUE,
                ]
                if invoice.status not in payable_statuses:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invoice {invoice.invoice_number} is not payable",
                    )
                if alloc.amount > invoice.balance_due:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Allocation exceeds balance due on {invoice.invoice_number}",
                    )

        # Handle WHT amounts
        wht_amount = input.wht_amount or Decimal("0")
        net_amount = input.amount

        if input.gross_amount is not None:
            gross_amount = input.gross_amount
            expected_wht = gross_amount - net_amount
            if wht_amount > Decimal("0") and abs(expected_wht - wht_amount) > Decimal(
                "0.01"
            ):
                raise HTTPException(
                    status_code=400,
                    detail=f"WHT amount ({wht_amount}) doesn't match gross - net ({expected_wht})",
                )
            if wht_amount == Decimal("0") and gross_amount != net_amount:
                wht_amount = expected_wht
        else:
            gross_amount = net_amount + wht_amount

        # Calculate functional currency amount
        exchange_rate = input.exchange_rate or Decimal("1.0")
        functional_amount = net_amount * exchange_rate

        # Update payment fields
        payment.customer_id = customer_id
        payment.payment_date = input.payment_date
        payment.payment_method = input.payment_method
        payment.currency_code = input.currency_code
        payment.gross_amount = gross_amount
        payment.amount = net_amount
        payment.wht_code_id = input.wht_code_id
        payment.wht_amount = wht_amount
        payment.wht_certificate_number = input.wht_certificate_number
        payment.exchange_rate = exchange_rate
        payment.functional_currency_amount = functional_amount
        payment.bank_account_id = input.bank_account_id
        payment.reference = input.reference
        payment.description = input.description

        # Delete existing allocations and recreate
        db.query(PaymentAllocation).filter(
            PaymentAllocation.payment_id == pay_id
        ).delete()

        # Create new allocations
        for alloc in input.allocations:
            allocation = PaymentAllocation(
                payment_id=pay_id,
                invoice_id=coerce_uuid(alloc.invoice_id),
                allocated_amount=alloc.amount,
            )
            db.add(allocation)

        db.commit()
        db.refresh(payment)

        return payment

    @staticmethod
    def get(
        db: Session,
        payment_id: str,
    ) -> CustomerPayment:
        """Get a payment by ID."""
        payment = db.get(CustomerPayment, coerce_uuid(payment_id))
        if not payment:
            raise HTTPException(status_code=404, detail="Payment not found")
        return payment

    @staticmethod
    def get_payment_allocations(
        db: Session,
        organization_id: UUID,
        payment_id: UUID,
    ) -> list[PaymentAllocation]:
        """Get allocations for a payment."""
        org_id = coerce_uuid(organization_id)
        pay_id = coerce_uuid(payment_id)

        payment = db.get(CustomerPayment, pay_id)
        if not payment or payment.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Payment not found")

        return (
            db.query(PaymentAllocation)
            .filter(PaymentAllocation.payment_id == pay_id)
            .all()
        )

    @staticmethod
    def delete_receipt(
        db: Session,
        organization_id: UUID,
        receipt_id: UUID,
    ) -> None:
        """Delete a receipt (PENDING only)."""
        org_id = coerce_uuid(organization_id)
        pay_id = coerce_uuid(receipt_id)

        payment = db.get(CustomerPayment, pay_id)
        if not payment or payment.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Receipt not found")

        if payment.status != PaymentStatus.PENDING:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot delete receipt with status '{payment.status.value}'. "
                    "Only draft receipts can be deleted."
                ),
            )

        db.query(PaymentAllocation).filter(
            PaymentAllocation.payment_id == pay_id
        ).delete()
        db.delete(payment)
        db.flush()
        db.commit()

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        customer_id: str | None = None,
        status: PaymentStatus | None = None,
        payment_method: PaymentMethod | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CustomerPayment]:
        """List payments with optional filters."""
        query = db.query(CustomerPayment)

        if organization_id:
            query = query.filter(
                CustomerPayment.organization_id == coerce_uuid(organization_id)
            )

        if customer_id:
            query = query.filter(
                CustomerPayment.customer_id == coerce_uuid(customer_id)
            )

        if status:
            query = query.filter(CustomerPayment.status == status)

        if payment_method:
            query = query.filter(CustomerPayment.payment_method == payment_method)

        if from_date:
            query = query.filter(CustomerPayment.payment_date >= from_date)

        if to_date:
            query = query.filter(CustomerPayment.payment_date <= to_date)

        query = query.order_by(CustomerPayment.payment_date.desc())
        return query.limit(limit).offset(offset).all()


# Module-level singleton instance
customer_payment_service = CustomerPaymentService()
