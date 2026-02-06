"""
SupplierPaymentService - AP payment processing.

Manages payment creation, approval, posting, and allocation to invoices.
"""

from __future__ import annotations

import logging
import uuid as uuid_lib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.finance.ap.ap_payment_allocation import APPaymentAllocation
from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
)
from app.models.finance.ap.supplier_payment import (
    APPaymentMethod,
    APPaymentStatus,
    SupplierPayment,
)
from app.models.finance.audit.audit_log import AuditAction
from app.models.finance.core_config.numbering_sequence import SequenceType
from app.services.audit_dispatcher import fire_audit_event
from app.services.common import coerce_uuid
from app.services.finance.platform.sequence import SequenceService
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class PaymentAllocationInput:
    """Input for allocating payment to an invoice."""

    invoice_id: UUID
    amount: Decimal


@dataclass
class SupplierPaymentInput:
    """Input for creating a supplier payment."""

    supplier_id: UUID
    payment_date: date
    payment_method: APPaymentMethod
    currency_code: str
    amount: Decimal  # Net amount paid (after WHT deduction)
    bank_account_id: UUID
    allocations: list[PaymentAllocationInput] = field(default_factory=list)
    exchange_rate: Optional[Decimal] = None
    reference: Optional[str] = None
    description: Optional[str] = None
    # Withholding Tax (WHT) - when we withhold tax from supplier payment
    gross_amount: Optional[Decimal] = (
        None  # Invoice amount before WHT; defaults to amount if no WHT
    )
    wht_code_id: Optional[UUID] = None  # WHT tax code applied
    wht_amount: Decimal = field(default_factory=lambda: Decimal("0"))  # WHT withheld
    # Legacy field - maps to wht_amount for backward compatibility
    withholding_tax_amount: Optional[Decimal] = None
    correlation_id: Optional[str] = None


class SupplierPaymentService(ListResponseMixin):
    """
    Service for supplier payment processing.

    Manages payment creation, approval, posting, and invoice allocation.
    """

    @staticmethod
    def create_payment(
        db: Session,
        organization_id: UUID,
        input: SupplierPaymentInput,
        created_by_user_id: UUID,
    ) -> SupplierPayment:
        """
        Create a new supplier payment.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Payment input data
            created_by_user_id: User creating the payment

        Returns:
            Created SupplierPayment

        Raises:
            HTTPException(400): If validation fails
            HTTPException(404): If supplier not found
        """
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(created_by_user_id)
        supplier_id = coerce_uuid(input.supplier_id)

        # Validate supplier
        supplier = db.get(Supplier, supplier_id)
        if not supplier or supplier.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Supplier not found")

        if not supplier.is_active:
            raise HTTPException(status_code=400, detail="Supplier is not active")

        # Resolve WHT amount (support legacy field)
        wht_amount = input.wht_amount
        if input.withholding_tax_amount is not None and wht_amount == Decimal("0"):
            wht_amount = input.withholding_tax_amount

        # Determine gross amount
        # If gross_amount provided, validate: gross = net + wht
        # If not provided, calculate: gross = amount + wht_amount
        gross_amount = input.gross_amount
        if gross_amount is None:
            gross_amount = input.amount + wht_amount
        else:
            # Validate the amounts match (with small tolerance for rounding)
            expected_net = gross_amount - wht_amount
            if abs(expected_net - input.amount) > Decimal("0.01"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Amount mismatch: gross ({gross_amount}) - WHT ({wht_amount}) != net ({input.amount})",
                )

        wht_code_id: Optional[UUID] = None

        # WHT code is required if WHT amount is non-zero
        if wht_amount > Decimal("0") and not input.wht_code_id:
            # Try to get default WHT code from supplier
            if supplier.withholding_tax_applicable and supplier.withholding_tax_code_id:
                wht_code_id = supplier.withholding_tax_code_id
            else:
                raise HTTPException(
                    status_code=400,
                    detail="WHT tax code is required when withholding tax amount is specified",
                )
        else:
            wht_code_id = input.wht_code_id

        # Validate allocations total - should match GROSS amount (invoice amount before WHT)
        if input.allocations:
            allocation_total = sum(a.amount for a in input.allocations)
            if allocation_total > gross_amount:
                raise HTTPException(
                    status_code=400,
                    detail="Allocation total exceeds gross payment amount",
                )

            # Validate invoices exist and are payable
            for alloc in input.allocations:
                invoice = db.get(SupplierInvoice, coerce_uuid(alloc.invoice_id))
                if not invoice or invoice.organization_id != org_id:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Invoice {alloc.invoice_id} not found",
                    )
                if invoice.supplier_id != supplier_id:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invoice {invoice.invoice_number} belongs to different supplier",
                    )
                if invoice.status not in [
                    SupplierInvoiceStatus.POSTED,
                    SupplierInvoiceStatus.PARTIALLY_PAID,
                ]:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invoice {invoice.invoice_number} is not payable",
                    )
                if alloc.amount > invoice.balance_due:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Allocation exceeds balance due on {invoice.invoice_number}",
                    )

        # Generate payment number
        payment_number = SequenceService.get_next_number(
            db, org_id, SequenceType.PAYMENT
        )

        # Calculate functional currency amount
        exchange_rate = input.exchange_rate or Decimal("1.0")
        functional_amount = input.amount * exchange_rate

        # Create payment
        payment = SupplierPayment(
            organization_id=org_id,
            supplier_id=supplier_id,
            payment_number=payment_number,
            payment_date=input.payment_date,
            payment_method=input.payment_method,
            currency_code=input.currency_code,
            amount=input.amount,  # Net amount paid to bank
            exchange_rate=exchange_rate,
            functional_currency_amount=functional_amount,
            bank_account_id=input.bank_account_id,
            reference=input.reference,
            status=APPaymentStatus.DRAFT,
            # WHT fields
            gross_amount=gross_amount,  # Invoice amount before WHT
            withholding_tax_amount=wht_amount,
            withholding_tax_code_id=wht_code_id,
            created_by_user_id=user_id,
            correlation_id=input.correlation_id or str(uuid_lib.uuid4()),
        )

        db.add(payment)
        db.flush()  # Get payment ID

        # Create allocations
        for alloc in input.allocations:
            allocation = APPaymentAllocation(
                payment_id=payment.payment_id,
                invoice_id=coerce_uuid(alloc.invoice_id),
                allocated_amount=alloc.amount,
            )
            db.add(allocation)

        db.commit()
        db.refresh(payment)

        fire_audit_event(
            db=db,
            organization_id=org_id,
            table_schema="ap",
            table_name="supplier_payment",
            record_id=str(payment.payment_id),
            action=AuditAction.INSERT,
            new_values={
                "payment_number": payment.payment_number,
                "amount": str(payment.amount),
            },
            user_id=user_id,
        )

        return payment

    @staticmethod
    def approve_payment(
        db: Session,
        organization_id: UUID,
        payment_id: UUID,
        approved_by_user_id: UUID,
    ) -> SupplierPayment:
        """
        Approve a payment for processing.

        Args:
            db: Database session
            organization_id: Organization scope
            payment_id: Payment to approve
            approved_by_user_id: User approving

        Returns:
            Updated SupplierPayment

        Raises:
            HTTPException: If validation fails
        """
        org_id = coerce_uuid(organization_id)
        pay_id = coerce_uuid(payment_id)
        user_id = coerce_uuid(approved_by_user_id)

        payment = db.get(SupplierPayment, pay_id)
        if not payment or payment.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Payment not found")

        if payment.status not in [APPaymentStatus.DRAFT, APPaymentStatus.PENDING]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot approve payment with status '{payment.status.value}'",
            )

        # Segregation of Duties check
        if payment.created_by_user_id == user_id:
            raise HTTPException(
                status_code=400,
                detail="Segregation of duties violation: creator cannot approve",
            )

        old_status = payment.status.value
        payment.status = APPaymentStatus.APPROVED
        payment.approved_by_user_id = user_id
        payment.approved_at = datetime.now(timezone.utc)

        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=db,
                organization_id=org_id,
                entity_type="PAYMENT",
                entity_id=payment.payment_id,
                event="ON_APPROVAL",
                old_values={"status": "DRAFT"},
                new_values={"status": "APPROVED"},
                user_id=user_id,
            )
        except Exception:
            pass

        db.commit()
        db.refresh(payment)

        fire_audit_event(
            db=db,
            organization_id=org_id,
            table_schema="ap",
            table_name="supplier_payment",
            record_id=str(payment.payment_id),
            action=AuditAction.UPDATE,
            old_values={"status": old_status},
            new_values={"status": "APPROVED"},
            user_id=user_id,
        )

        return payment

    @staticmethod
    def post_payment(
        db: Session,
        organization_id: UUID,
        payment_id: UUID,
        posted_by_user_id: UUID,
        posting_date: Optional[date] = None,
    ) -> SupplierPayment:
        """
        Post an approved payment to the general ledger.

        Args:
            db: Database session
            organization_id: Organization scope
            payment_id: Payment to post
            posted_by_user_id: User posting
            posting_date: Optional posting date

        Returns:
            Updated SupplierPayment
        """
        from app.services.finance.ap.ap_posting_adapter import APPostingAdapter

        org_id = coerce_uuid(organization_id)
        pay_id = coerce_uuid(payment_id)
        user_id = coerce_uuid(posted_by_user_id)

        payment = db.get(SupplierPayment, pay_id)
        if not payment or payment.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Payment not found")

        if payment.status != APPaymentStatus.APPROVED:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot post payment with status '{payment.status.value}'",
            )

        # Post via adapter
        result = APPostingAdapter.post_payment(
            db=db,
            organization_id=org_id,
            payment_id=pay_id,
            posting_date=posting_date or payment.payment_date,
            posted_by_user_id=user_id,
        )

        if not result.success:
            raise HTTPException(status_code=400, detail=result.message)

        # Update payment status
        payment.status = APPaymentStatus.SENT
        payment.posted_by_user_id = user_id
        payment.posted_at = datetime.now(timezone.utc)
        payment.journal_entry_id = result.journal_entry_id
        payment.posting_batch_id = result.posting_batch_id

        # Apply allocations to invoices
        allocations = (
            db.query(APPaymentAllocation)
            .filter(APPaymentAllocation.payment_id == pay_id)
            .all()
        )

        for alloc in allocations:
            invoice = db.get(SupplierInvoice, alloc.invoice_id)
            if invoice:
                invoice.amount_paid += alloc.allocated_amount
                if invoice.amount_paid >= invoice.total_amount:
                    invoice.status = SupplierInvoiceStatus.PAID
                else:
                    invoice.status = SupplierInvoiceStatus.PARTIALLY_PAID

        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=db,
                organization_id=org_id,
                entity_type="PAYMENT",
                entity_id=payment.payment_id,
                event="ON_STATUS_CHANGE",
                old_values={"status": "APPROVED"},
                new_values={"status": "SENT"},
                user_id=user_id,
            )
        except Exception:
            pass

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
    ) -> SupplierPayment:
        """
        Void a payment.

        Args:
            db: Database session
            organization_id: Organization scope
            payment_id: Payment to void
            voided_by_user_id: User voiding
            reason: Reason for voiding

        Returns:
            Updated SupplierPayment
        """
        org_id = coerce_uuid(organization_id)
        pay_id = coerce_uuid(payment_id)

        payment = db.get(SupplierPayment, pay_id)
        if not payment or payment.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Payment not found")

        if payment.status in [APPaymentStatus.CLEARED, APPaymentStatus.VOID]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot void payment with status '{payment.status.value}'",
            )

        # If payment was posted, reverse the allocations
        if payment.status == APPaymentStatus.SENT:
            allocations = (
                db.query(APPaymentAllocation)
                .filter(APPaymentAllocation.payment_id == pay_id)
                .all()
            )

            for alloc in allocations:
                invoice = db.get(SupplierInvoice, alloc.invoice_id)
                if invoice:
                    invoice.amount_paid -= alloc.allocated_amount
                    if invoice.amount_paid <= Decimal("0"):
                        invoice.status = SupplierInvoiceStatus.POSTED
                    else:
                        invoice.status = SupplierInvoiceStatus.PARTIALLY_PAID

        old_status = payment.status.value
        payment.status = APPaymentStatus.VOID

        db.commit()
        db.refresh(payment)

        fire_audit_event(
            db=db,
            organization_id=org_id,
            table_schema="ap",
            table_name="supplier_payment",
            record_id=str(payment.payment_id),
            action=AuditAction.UPDATE,
            old_values={"status": old_status},
            new_values={"status": "VOID"},
            user_id=coerce_uuid(voided_by_user_id),
        )

        return payment

    @staticmethod
    def mark_cleared(
        db: Session,
        organization_id: UUID,
        payment_id: UUID,
        cleared_date: date,
    ) -> SupplierPayment:
        """
        Mark a payment as cleared (bank reconciliation).

        Args:
            db: Database session
            organization_id: Organization scope
            payment_id: Payment to mark cleared
            cleared_date: Date cleared

        Returns:
            Updated SupplierPayment
        """
        org_id = coerce_uuid(organization_id)
        pay_id = coerce_uuid(payment_id)

        payment = db.get(SupplierPayment, pay_id)
        if not payment or payment.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Payment not found")

        if payment.status != APPaymentStatus.SENT:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot clear payment with status '{payment.status.value}'",
            )

        payment.status = APPaymentStatus.CLEARED

        db.commit()
        db.refresh(payment)

        return payment

    @staticmethod
    def get(
        db: Session,
        payment_id: str,
    ) -> SupplierPayment:
        """
        Get a payment by ID.

        Args:
            db: Database session
            payment_id: Payment ID

        Returns:
            SupplierPayment

        Raises:
            HTTPException(404): If not found
        """
        payment = db.get(SupplierPayment, coerce_uuid(payment_id))
        if not payment:
            raise HTTPException(status_code=404, detail="Payment not found")
        return payment

    @staticmethod
    def get_payment_allocations(
        db: Session,
        organization_id: UUID,
        payment_id: UUID,
    ) -> List[APPaymentAllocation]:
        """
        Get allocations for a payment.

        Args:
            db: Database session
            organization_id: Organization scope
            payment_id: Payment ID

        Returns:
            List of APPaymentAllocation objects
        """
        org_id = coerce_uuid(organization_id)
        pay_id = coerce_uuid(payment_id)

        payment = db.get(SupplierPayment, pay_id)
        if not payment or payment.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Payment not found")

        return (
            db.query(APPaymentAllocation)
            .filter(APPaymentAllocation.payment_id == pay_id)
            .all()
        )

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        supplier_id: Optional[str] = None,
        status: Optional[APPaymentStatus] = None,
        payment_method: Optional[APPaymentMethod] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[SupplierPayment]:
        """
        List payments with optional filters.

        Args:
            db: Database session
            organization_id: Filter by organization
            supplier_id: Filter by supplier
            status: Filter by status
            payment_method: Filter by payment method
            from_date: Filter by payment date from
            to_date: Filter by payment date to
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of SupplierPayment objects
        """
        query = db.query(SupplierPayment)

        if organization_id:
            query = query.filter(
                SupplierPayment.organization_id == coerce_uuid(organization_id)
            )

        if supplier_id:
            query = query.filter(
                SupplierPayment.supplier_id == coerce_uuid(supplier_id)
            )

        if status:
            query = query.filter(SupplierPayment.status == status)

        if payment_method:
            query = query.filter(SupplierPayment.payment_method == payment_method)

        if from_date:
            query = query.filter(SupplierPayment.payment_date >= from_date)

        if to_date:
            query = query.filter(SupplierPayment.payment_date <= to_date)

        query = query.order_by(SupplierPayment.payment_date.desc())
        return query.limit(limit).offset(offset).all()


# Module-level singleton instance
supplier_payment_service = SupplierPaymentService()
