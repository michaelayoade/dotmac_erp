"""
Batch Transfer Service.

Handles bulk expense reimbursement transfers via Paystack.
"""
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.expense.expense_claim import ExpenseClaim, ExpenseClaimStatus
from app.models.finance.payments.transfer_batch import (
    TransferBatch,
    TransferBatchItem,
    TransferBatchStatus,
    TransferBatchItemStatus,
)
from app.models.finance.payments.payment_intent import (
    PaymentDirection,
    PaymentIntent,
    PaymentIntentStatus,
)
from app.models.domain_settings import SettingDomain
from app.services.common import coerce_uuid
from app.models.finance.core_config.numbering_sequence import SequenceType
from app.services.finance.platform.sequence import SequenceService
from app.services.finance.payments.paystack_client import PaystackClient, PaystackConfig
from app.services.settings_spec import resolve_value

logger = logging.getLogger(__name__)


@dataclass
class BatchTransferResult:
    """Result of batch transfer operation."""

    success: bool
    batch_id: Optional[UUID] = None
    message: str = ""
    initiated_count: int = 0
    failed_count: int = 0


class BatchTransferService:
    """
    Service for batch transfer operations.

    Creates and processes batches of expense reimbursement transfers.
    """

    def __init__(self, db: Session, organization_id: UUID):
        self.db = db
        self.organization_id = coerce_uuid(organization_id)

    def create_batch(
        self,
        expense_claim_ids: list[UUID],
        batch_date: date,
        created_by_user_id: UUID,
        description: Optional[str] = None,
    ) -> TransferBatch:
        """
        Create a new transfer batch from expense claims.

        Args:
            expense_claim_ids: List of approved expense claim IDs to include
            batch_date: Date for the batch
            created_by_user_id: User creating the batch
            description: Optional batch description

        Returns:
            TransferBatch with items

        Raises:
            HTTPException: If validation fails
        """
        user_id = coerce_uuid(created_by_user_id)

        # Get transfer bank account from settings
        bank_account_id = resolve_value(
            self.db, SettingDomain.payments, "paystack_transfer_bank_account_id"
        )
        if not bank_account_id:
            raise HTTPException(
                status_code=400,
                detail="Transfer bank account not configured in payment settings",
            )

        bank_account_uuid = coerce_uuid(bank_account_id)

        # Generate batch number
        batch_number = SequenceService.get_next_number(
            self.db,
            self.organization_id,
            SequenceType.PAYMENT,
        )

        # Create batch
        batch = TransferBatch(
            batch_id=uuid4(),
            organization_id=self.organization_id,
            batch_number=batch_number,
            batch_date=batch_date,
            description=description or f"Expense reimbursement batch {batch_number}",
            bank_account_id=bank_account_uuid,
            currency_code="NGN",
            status=TransferBatchStatus.DRAFT,
            created_by_user_id=user_id,
        )

        self.db.add(batch)
        self.db.flush()

        # Add items from expense claims
        sequence = 0
        for claim_id in expense_claim_ids:
            claim = self.db.get(ExpenseClaim, coerce_uuid(claim_id))
            if not claim or claim.organization_id != self.organization_id:
                logger.warning(f"Expense claim {claim_id} not found, skipping")
                continue

            if claim.status != ExpenseClaimStatus.APPROVED:
                logger.warning(f"Claim {claim.claim_number} not approved, skipping")
                continue

            if not claim.net_payable_amount or claim.net_payable_amount <= Decimal("0"):
                logger.warning(f"Claim {claim.claim_number} has no payable amount, skipping")
                continue

            # Get employee bank details
            if not claim.recipient_bank_code or not claim.recipient_account_number:
                logger.warning(f"Claim {claim.claim_number} missing bank details, skipping")
                continue

            # Get employee name
            employee_name = "Unknown"
            if claim.employee:
                employee_name = claim.employee.full_name

            sequence += 1
            item = TransferBatchItem(
                item_id=uuid4(),
                batch_id=batch.batch_id,
                sequence=sequence,
                expense_claim_id=claim.claim_id,
                recipient_name=employee_name,
                recipient_bank_code=claim.recipient_bank_code,
                recipient_account_number=claim.recipient_account_number,
                amount=claim.net_payable_amount,
                currency_code="NGN",
                status=TransferBatchItemStatus.PENDING,
            )
            self.db.add(item)

        self.db.flush()

        # Update totals
        batch.update_totals()
        self.db.flush()

        logger.info(
            f"Created transfer batch {batch.batch_number} with {batch.total_transfers} items",
            extra={
                "batch_id": str(batch.batch_id),
                "total_amount": str(batch.total_amount),
            },
        )

        return batch

    def submit_for_approval(
        self,
        batch_id: UUID,
    ) -> TransferBatch:
        """Submit a batch for approval."""
        batch = self._get_batch(batch_id)

        if batch.status != TransferBatchStatus.DRAFT:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot submit batch with status '{batch.status.value}'",
            )

        if batch.total_transfers == 0:
            raise HTTPException(
                status_code=400,
                detail="Cannot submit empty batch",
            )

        batch.status = TransferBatchStatus.PENDING_APPROVAL
        self.db.flush()

        return batch

    def approve_batch(
        self,
        batch_id: UUID,
        approved_by_user_id: UUID,
    ) -> TransferBatch:
        """Approve a batch for processing."""
        batch = self._get_batch(batch_id)
        user_id = coerce_uuid(approved_by_user_id)

        if batch.status != TransferBatchStatus.PENDING_APPROVAL:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot approve batch with status '{batch.status.value}'",
            )

        batch.status = TransferBatchStatus.APPROVED
        batch.approved_by_user_id = user_id
        batch.approved_at = datetime.now(timezone.utc)
        self.db.flush()

        logger.info(f"Approved transfer batch {batch.batch_number}")

        return batch

    def process_batch(
        self,
        batch_id: UUID,
        paystack_config: PaystackConfig,
    ) -> BatchTransferResult:
        """
        Process an approved batch - initiate all transfers.

        Creates payment intents and initiates transfers for each item.
        Paystack doesn't have a true bulk transfer API, so we process sequentially
        but could be parallelized in the future.

        Args:
            batch_id: Batch to process
            paystack_config: Paystack credentials

        Returns:
            BatchTransferResult with counts
        """
        batch = self._get_batch(batch_id)

        if batch.status != TransferBatchStatus.APPROVED:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot process batch with status '{batch.status.value}'",
            )

        batch.status = TransferBatchStatus.PROCESSING
        batch.processed_at = datetime.now(timezone.utc)
        self.db.flush()

        initiated_count = 0
        failed_count = 0

        with PaystackClient(paystack_config) as client:
            for item in batch.items:
                try:
                    self._process_batch_item(item, client, batch)
                    initiated_count += 1
                except Exception as e:
                    logger.exception(f"Failed to process batch item {item.item_id}")
                    item.status = TransferBatchItemStatus.FAILED
                    item.error_message = str(e)[:500]
                    failed_count += 1

                self.db.flush()

        # Update batch status
        # completed_count only counts fully COMPLETED items (via webhook confirmation)
        # During process_batch, items move to PROCESSING status; webhooks update to COMPLETED
        batch.completed_count = sum(
            1 for item in batch.items
            if item.status == TransferBatchItemStatus.COMPLETED
        )
        batch.failed_count = failed_count
        processing_count = sum(
            1 for item in batch.items
            if item.status == TransferBatchItemStatus.PROCESSING
        )

        if failed_count == len(batch.items):
            batch.status = TransferBatchStatus.FAILED
        elif batch.completed_count == len(batch.items):
            # All items completed successfully
            batch.status = TransferBatchStatus.COMPLETED
        elif batch.completed_count + failed_count == len(batch.items):
            # All items finalized but some failed
            batch.status = TransferBatchStatus.PARTIALLY_COMPLETED
        elif processing_count > 0:
            # Some items still processing (awaiting webhook confirmation)
            batch.status = TransferBatchStatus.PROCESSING
        elif failed_count > 0:
            batch.status = TransferBatchStatus.PARTIALLY_COMPLETED
        else:
            batch.status = TransferBatchStatus.PROCESSING

        self.db.flush()

        logger.info(
            f"Processed batch {batch.batch_number}: {initiated_count} initiated, {failed_count} failed",
            extra={
                "batch_id": str(batch.batch_id),
                "initiated": initiated_count,
                "failed": failed_count,
            },
        )

        return BatchTransferResult(
            success=initiated_count > 0,
            batch_id=batch.batch_id,
            message=f"Initiated {initiated_count} transfers, {failed_count} failed",
            initiated_count=initiated_count,
            failed_count=failed_count,
        )

    def _process_batch_item(
        self,
        item: TransferBatchItem,
        client: PaystackClient,
        batch: TransferBatch,
    ) -> None:
        """Process a single batch item."""
        # Verify account and create recipient
        account_info = client.resolve_account(
            account_number=item.recipient_account_number,
            bank_code=item.recipient_bank_code,
        )

        # Get claim for metadata
        claim = self.db.get(ExpenseClaim, item.expense_claim_id)
        if not claim:
            raise ValueError(f"Expense claim {item.expense_claim_id} not found")

        intent_metadata = {
            "batch_id": str(batch.batch_id),
            "batch_number": batch.batch_number,
            "claim_number": claim.claim_number,
            "claim_id": str(claim.claim_id),
            "employee_name": item.recipient_name,
        }

        # Create transfer recipient
        recipient = client.create_transfer_recipient(
            name=account_info.account_name,
            account_number=item.recipient_account_number,
            bank_code=item.recipient_bank_code,
            currency="NGN",
            description=f"Batch {batch.batch_number}: {item.recipient_name}",
            metadata=intent_metadata,
        )

        item.transfer_recipient_code = recipient.recipient_code

        # Generate reference
        short_uuid = uuid4().hex[:8]
        reference = f"BATCH-{batch.batch_number}-{item.sequence}-{short_uuid}"
        item.transfer_reference = reference

        # Create payment intent
        intent = PaymentIntent(
            intent_id=uuid4(),
            organization_id=self.organization_id,
            paystack_reference=reference,
            amount=item.amount,
            currency_code="NGN",
            email=claim.employee.work_email if claim.employee else "",
            direction=PaymentDirection.OUTBOUND,
            bank_account_id=batch.bank_account_id,
            source_type="EXPENSE_CLAIM",
            source_id=claim.claim_id,
            transfer_recipient_code=recipient.recipient_code,
            recipient_bank_code=item.recipient_bank_code,
            recipient_account_number=item.recipient_account_number,
            recipient_account_name=account_info.account_name,
            status=PaymentIntentStatus.PENDING,
            intent_metadata=intent_metadata,
        )

        self.db.add(intent)
        self.db.flush()

        item.payment_intent_id = intent.intent_id

        # Initiate transfer (use Decimal to avoid float precision issues)
        amount_kobo = int(
            (Decimal(item.amount) * Decimal("100")).to_integral_value(
                rounding=ROUND_HALF_UP
            )
        )
        result = client.initiate_transfer(
            amount=amount_kobo,
            recipient_code=recipient.recipient_code,
            reference=reference,
            reason=f"Expense reimbursement: {claim.claim_number}",
            currency="NGN",
        )

        # Update item and intent
        item.transfer_code = result.transfer_code
        item.status = TransferBatchItemStatus.PROCESSING
        item.processed_at = datetime.now(timezone.utc)

        intent.transfer_code = result.transfer_code
        intent.status = PaymentIntentStatus.PROCESSING

    def get_batch(self, batch_id: UUID) -> TransferBatch:
        """Get a batch by ID."""
        return self._get_batch(batch_id)

    def list_batches(
        self,
        status: Optional[TransferBatchStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TransferBatch]:
        """List batches for the organization."""
        query = (
            select(TransferBatch)
            .where(TransferBatch.organization_id == self.organization_id)
            .order_by(TransferBatch.created_at.desc())
            .limit(limit)
            .offset(offset)
        )

        if status:
            query = query.where(TransferBatch.status == status)

        return list(self.db.execute(query).scalars().all())

    def get_pending_claims_for_batch(self) -> list[ExpenseClaim]:
        """
        Get approved expense claims that can be added to a batch.

        Returns claims that are APPROVED and have bank details configured.
        """
        return list(
            self.db.execute(
                select(ExpenseClaim)
                .where(
                    ExpenseClaim.organization_id == self.organization_id,
                    ExpenseClaim.status == ExpenseClaimStatus.APPROVED,
                    ExpenseClaim.recipient_bank_code.isnot(None),
                    ExpenseClaim.recipient_account_number.isnot(None),
                    ExpenseClaim.net_payable_amount > Decimal("0"),
                )
                .order_by(ExpenseClaim.approved_on)
            ).scalars().all()
        )

    def _get_batch(self, batch_id: UUID) -> TransferBatch:
        """Get batch with validation."""
        batch = self.db.get(TransferBatch, coerce_uuid(batch_id))
        if not batch or batch.organization_id != self.organization_id:
            raise HTTPException(status_code=404, detail="Batch not found")
        return batch
