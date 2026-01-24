"""
PaymentBatchService - Bulk payment batch management.

Manages payment batch creation, approval, processing, and bank file generation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID
import uuid as uuid_lib

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.finance.ap.payment_batch import APPaymentBatch, APBatchStatus
from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
)
from app.models.finance.ap.supplier_payment import (
    SupplierPayment,
    APPaymentMethod,
    APPaymentStatus,
)
from app.models.finance.core_config.numbering_sequence import SequenceType
from app.services.common import coerce_uuid
from app.services.finance.platform.sequence import SequenceService
from app.services.response import ListResponseMixin


@dataclass
class BatchPaymentItem:
    """A payment to include in the batch."""

    supplier_id: UUID
    amount: Decimal
    invoice_ids: list[UUID] = field(default_factory=list)
    reference: Optional[str] = None


@dataclass
class PaymentBatchInput:
    """Input for creating a payment batch."""

    batch_date: date
    payment_method: str
    bank_account_id: UUID
    currency_code: str
    payments: list[BatchPaymentItem] = field(default_factory=list)


class PaymentBatchService(ListResponseMixin):
    """
    Service for bulk payment batch management.

    Enables grouping multiple supplier payments into batches for
    efficient processing and bank file generation.
    """

    @staticmethod
    def create_batch(
        db: Session,
        organization_id: UUID,
        input: PaymentBatchInput,
        created_by_user_id: UUID,
    ) -> APPaymentBatch:
        """
        Create a new payment batch.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Batch input data
            created_by_user_id: User creating the batch

        Returns:
            Created APPaymentBatch

        Raises:
            HTTPException(400): If validation fails
        """
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(created_by_user_id)

        if not input.payments:
            raise HTTPException(
                status_code=400,
                detail="Payment batch must have at least one payment"
            )

        # Generate batch number
        batch_number = SequenceService.get_next_number(
            db, org_id, SequenceType.PAYMENT
        )
        batch_number = f"BATCH-{batch_number}"

        # Calculate totals
        total_amount = sum(p.amount for p in input.payments)
        total_payments = len(input.payments)

        # Create batch
        batch = APPaymentBatch(
            organization_id=org_id,
            batch_number=batch_number,
            batch_date=input.batch_date,
            payment_method=input.payment_method,
            bank_account_id=input.bank_account_id,
            currency_code=input.currency_code,
            total_payments=total_payments,
            total_amount=total_amount,
            status=APBatchStatus.DRAFT,
            created_by_user_id=user_id,
        )
        db.add(batch)
        db.flush()

        db.commit()
        db.refresh(batch)

        return batch

    @staticmethod
    def add_payment_to_batch(
        db: Session,
        organization_id: UUID,
        batch_id: UUID,
        payment_id: UUID,
    ) -> APPaymentBatch:
        """
        Add an existing payment to a batch.

        Args:
            db: Database session
            organization_id: Organization scope
            batch_id: Batch to add to
            payment_id: Payment to add

        Returns:
            Updated APPaymentBatch
        """
        org_id = coerce_uuid(organization_id)
        batch_id = coerce_uuid(batch_id)
        payment_id = coerce_uuid(payment_id)

        batch = db.query(APPaymentBatch).filter(
            APPaymentBatch.batch_id == batch_id,
            APPaymentBatch.organization_id == org_id,
        ).first()

        if not batch:
            raise HTTPException(status_code=404, detail="Payment batch not found")

        if batch.status != APBatchStatus.DRAFT:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot modify batch in {batch.status.value} status"
            )

        payment = db.query(SupplierPayment).filter(
            SupplierPayment.payment_id == payment_id,
            SupplierPayment.organization_id == org_id,
        ).first()

        if not payment:
            raise HTTPException(status_code=404, detail="Payment not found")

        if payment.payment_batch_id is not None:
            raise HTTPException(
                status_code=400,
                detail="Payment already belongs to a batch"
            )

        if payment.status not in [APPaymentStatus.DRAFT, APPaymentStatus.PENDING]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot add payment with status {payment.status.value} to batch"
            )

        # Add payment to batch
        payment.payment_batch_id = batch_id

        # Update batch totals
        batch.total_payments += 1
        batch.total_amount += payment.amount

        db.commit()
        db.refresh(batch)

        return batch

    @staticmethod
    def remove_payment_from_batch(
        db: Session,
        organization_id: UUID,
        batch_id: UUID,
        payment_id: UUID,
    ) -> APPaymentBatch:
        """
        Remove a payment from a batch.

        Args:
            db: Database session
            organization_id: Organization scope
            batch_id: Batch to remove from
            payment_id: Payment to remove

        Returns:
            Updated APPaymentBatch
        """
        org_id = coerce_uuid(organization_id)
        batch_id = coerce_uuid(batch_id)
        payment_id = coerce_uuid(payment_id)

        batch = db.query(APPaymentBatch).filter(
            APPaymentBatch.batch_id == batch_id,
            APPaymentBatch.organization_id == org_id,
        ).first()

        if not batch:
            raise HTTPException(status_code=404, detail="Payment batch not found")

        if batch.status != APBatchStatus.DRAFT:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot modify batch in {batch.status.value} status"
            )

        payment = db.query(SupplierPayment).filter(
            SupplierPayment.payment_id == payment_id,
            SupplierPayment.payment_batch_id == batch_id,
        ).first()

        if not payment:
            raise HTTPException(
                status_code=404,
                detail="Payment not found in this batch"
            )

        # Remove payment from batch
        payment.payment_batch_id = None

        # Update batch totals
        batch.total_payments -= 1
        batch.total_amount -= payment.amount

        db.commit()
        db.refresh(batch)

        return batch

    @staticmethod
    def approve_batch(
        db: Session,
        organization_id: UUID,
        batch_id: UUID,
        approved_by_user_id: UUID,
    ) -> APPaymentBatch:
        """
        Approve a payment batch for processing.

        Args:
            db: Database session
            organization_id: Organization scope
            batch_id: Batch to approve
            approved_by_user_id: User approving

        Returns:
            Updated APPaymentBatch
        """
        org_id = coerce_uuid(organization_id)
        batch_id = coerce_uuid(batch_id)
        user_id = coerce_uuid(approved_by_user_id)

        batch = db.query(APPaymentBatch).filter(
            APPaymentBatch.batch_id == batch_id,
            APPaymentBatch.organization_id == org_id,
        ).first()

        if not batch:
            raise HTTPException(status_code=404, detail="Payment batch not found")

        if batch.status != APBatchStatus.DRAFT:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot approve batch in {batch.status.value} status"
            )

        # Segregation of Duties check
        if batch.created_by_user_id == user_id:
            raise HTTPException(
                status_code=400,
                detail="Segregation of duties violation: creator cannot approve"
            )

        # Verify batch has payments
        payment_count = db.query(SupplierPayment).filter(
            SupplierPayment.payment_batch_id == batch_id
        ).count()

        if payment_count == 0:
            raise HTTPException(
                status_code=400,
                detail="Cannot approve empty batch"
            )

        batch.status = APBatchStatus.APPROVED
        batch.approved_by_user_id = user_id
        batch.approved_at = datetime.now(timezone.utc)

        # Also approve all payments in the batch
        payments = db.query(SupplierPayment).filter(
            SupplierPayment.payment_batch_id == batch_id
        ).all()

        for payment in payments:
            if payment.status == APPaymentStatus.DRAFT:
                payment.status = APPaymentStatus.APPROVED
                payment.approved_by_user_id = user_id
                payment.approved_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(batch)

        return batch

    @staticmethod
    def process_batch(
        db: Session,
        organization_id: UUID,
        batch_id: UUID,
        processed_by_user_id: UUID,
    ) -> APPaymentBatch:
        """
        Process all payments in a batch.

        Args:
            db: Database session
            organization_id: Organization scope
            batch_id: Batch to process
            processed_by_user_id: User processing

        Returns:
            Updated APPaymentBatch
        """
        from app.services.finance.ap.supplier_payment import SupplierPaymentService

        org_id = coerce_uuid(organization_id)
        batch_id = coerce_uuid(batch_id)
        user_id = coerce_uuid(processed_by_user_id)

        batch = db.query(APPaymentBatch).filter(
            APPaymentBatch.batch_id == batch_id,
            APPaymentBatch.organization_id == org_id,
        ).first()

        if not batch:
            raise HTTPException(status_code=404, detail="Payment batch not found")

        if batch.status != APBatchStatus.APPROVED:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot process batch in {batch.status.value} status"
            )

        batch.status = APBatchStatus.PROCESSING

        # Process each payment
        payments = db.query(SupplierPayment).filter(
            SupplierPayment.payment_batch_id == batch_id,
            SupplierPayment.status == APPaymentStatus.APPROVED,
        ).all()

        all_success = True
        for payment in payments:
            try:
                SupplierPaymentService.post_payment(
                    db=db,
                    organization_id=org_id,
                    payment_id=payment.payment_id,
                    posted_by_user_id=user_id,
                )
            except HTTPException:
                all_success = False
                payment.status = APPaymentStatus.REJECTED

        if all_success:
            batch.status = APBatchStatus.COMPLETED
        else:
            batch.status = APBatchStatus.FAILED

        db.commit()
        db.refresh(batch)

        return batch

    @staticmethod
    def generate_bank_file(
        db: Session,
        organization_id: UUID,
        batch_id: UUID,
        file_format: str = "ACH",
    ) -> dict[str, Any]:
        """
        Generate bank file for a payment batch.

        Args:
            db: Database session
            organization_id: Organization scope
            batch_id: Batch to generate file for
            file_format: Bank file format (ACH, BACS, SEPA, etc.)

        Returns:
            Dictionary with file reference and content
        """
        org_id = coerce_uuid(organization_id)
        batch_id = coerce_uuid(batch_id)

        batch = db.query(APPaymentBatch).filter(
            APPaymentBatch.batch_id == batch_id,
            APPaymentBatch.organization_id == org_id,
        ).first()

        if not batch:
            raise HTTPException(status_code=404, detail="Payment batch not found")

        if batch.status not in [APBatchStatus.APPROVED, APBatchStatus.COMPLETED]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot generate bank file for batch in {batch.status.value} status"
            )

        # Get payments
        payments = db.query(SupplierPayment).filter(
            SupplierPayment.payment_batch_id == batch_id
        ).all()

        # Generate file reference
        file_reference = f"{file_format}-{batch.batch_number}-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        # Generate file content (simplified - actual implementation would vary by format)
        file_lines = [
            f"HEADER,{batch.batch_number},{batch.batch_date},{batch.total_amount},{batch.currency_code}",
        ]

        for payment in payments:
            supplier = db.query(Supplier).filter(
                Supplier.supplier_id == payment.supplier_id
            ).first()
            supplier_name = supplier.name if supplier else "Unknown"

            file_lines.append(
                f"PAYMENT,{payment.payment_number},{supplier_name},{payment.amount},{payment.reference or ''}"
            )

        file_lines.append(f"TRAILER,{len(payments)},{batch.total_amount}")

        # Update batch
        batch.bank_file_generated = True
        batch.bank_file_reference = file_reference
        batch.bank_file_generated_at = datetime.now(timezone.utc)

        db.commit()

        return {
            "file_reference": file_reference,
            "file_format": file_format,
            "content": "\n".join(file_lines),
            "payment_count": len(payments),
            "total_amount": str(batch.total_amount),
        }

    @staticmethod
    def get_batch_payments(
        db: Session,
        organization_id: UUID,
        batch_id: UUID,
    ) -> list[SupplierPayment]:
        """
        Get all payments in a batch.

        Args:
            db: Database session
            organization_id: Organization scope
            batch_id: Batch ID

        Returns:
            List of SupplierPayment objects
        """
        org_id = coerce_uuid(organization_id)
        batch_id = coerce_uuid(batch_id)

        batch = db.query(APPaymentBatch).filter(
            APPaymentBatch.batch_id == batch_id,
            APPaymentBatch.organization_id == org_id,
        ).first()

        if not batch:
            raise HTTPException(status_code=404, detail="Payment batch not found")

        return db.query(SupplierPayment).filter(
            SupplierPayment.payment_batch_id == batch_id
        ).order_by(SupplierPayment.payment_number).all()

    @staticmethod
    def get(db: Session, batch_id: str) -> Optional[APPaymentBatch]:
        """Get a payment batch by ID."""
        return db.query(APPaymentBatch).filter(
            APPaymentBatch.batch_id == coerce_uuid(batch_id)
        ).first()

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        status: Optional[APBatchStatus] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[APPaymentBatch]:
        """
        List payment batches with filters.

        Args:
            db: Database session
            organization_id: Filter by organization
            status: Filter by status
            from_date: Filter by start date
            to_date: Filter by end date
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of APPaymentBatch objects
        """
        query = db.query(APPaymentBatch)

        if organization_id:
            query = query.filter(
                APPaymentBatch.organization_id == coerce_uuid(organization_id)
            )

        if status:
            query = query.filter(APPaymentBatch.status == status)

        if from_date:
            query = query.filter(APPaymentBatch.batch_date >= from_date)

        if to_date:
            query = query.filter(APPaymentBatch.batch_date <= to_date)

        return query.order_by(APPaymentBatch.batch_date.desc()).offset(offset).limit(limit).all()


# Module-level instance
payment_batch_service = PaymentBatchService()
