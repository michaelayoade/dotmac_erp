"""
Transfer Batch Model - Payments Schema.

Batch processing for multiple Paystack transfers (e.g., expense reimbursements).
"""
import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, TYPE_CHECKING

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.finance.payments.payment_intent import PaymentIntent


class TransferBatchStatus(str, enum.Enum):
    """Status of a transfer batch."""

    DRAFT = "DRAFT"  # Being assembled
    PENDING_APPROVAL = "PENDING_APPROVAL"  # Awaiting approval
    APPROVED = "APPROVED"  # Approved, ready to process
    PROCESSING = "PROCESSING"  # Transfers being initiated
    COMPLETED = "COMPLETED"  # All transfers completed
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"  # Some transfers failed
    FAILED = "FAILED"  # All transfers failed


class TransferBatchItemStatus(str, enum.Enum):
    """Status of an individual transfer in a batch."""

    PENDING = "PENDING"  # Awaiting batch processing
    PROCESSING = "PROCESSING"  # Transfer initiated
    COMPLETED = "COMPLETED"  # Transfer successful
    FAILED = "FAILED"  # Transfer failed


class TransferBatch(Base):
    """
    Transfer Batch - group of transfers to process together.

    Used for bulk expense reimbursements via Paystack's bulk transfer API.
    """

    __tablename__ = "transfer_batch"
    __table_args__ = (
        UniqueConstraint("organization_id", "batch_number", name="uq_transfer_batch"),
        Index("idx_transfer_batch_status", "organization_id", "status"),
        {"schema": "payments"},
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core_org.organization.organization_id"),
        nullable=False,
        index=True,
    )

    # Batch identification
    batch_number: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    batch_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
    )

    # Source bank account for transfers
    bank_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("banking.bank_accounts.bank_account_id"),
        nullable=False,
        comment="Source bank account for transfers",
    )
    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="NGN",
    )

    # Batch totals
    total_transfers: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        default=Decimal("0"),
    )
    total_fees: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        default=Decimal("0"),
        comment="Total fees charged for all transfers",
    )

    # Completion tracking
    completed_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    failed_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    # Status
    status: Mapped[TransferBatchStatus] = mapped_column(
        Enum(
            TransferBatchStatus,
            name="transfer_batch_status",
            schema="payments",
            create_type=False,
        ),
        nullable=False,
        default=TransferBatchStatus.DRAFT,
    )

    # Workflow
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Paystack batch reference (if using bulk API)
    paystack_batch_reference: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )

    # Notes
    notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )

    # Relationships
    items: Mapped[list["TransferBatchItem"]] = relationship(
        "TransferBatchItem",
        back_populates="batch",
        order_by="TransferBatchItem.sequence",
    )

    def update_totals(self) -> None:
        """Recalculate batch totals from items."""
        self.total_transfers = len(self.items)
        self.total_amount = sum(item.amount for item in self.items)
        self.completed_count = sum(
            1 for item in self.items if item.status == TransferBatchItemStatus.COMPLETED
        )
        self.failed_count = sum(
            1 for item in self.items if item.status == TransferBatchItemStatus.FAILED
        )
        # Sum fees from completed items (fees are only known after completion)
        self.total_fees = sum(
            item.fee_amount or Decimal("0")
            for item in self.items
            if item.status == TransferBatchItemStatus.COMPLETED and item.fee_amount
        )

    def __repr__(self) -> str:
        return f"<TransferBatch {self.batch_number}: {self.status.value}>"


class TransferBatchItem(Base):
    """
    Transfer Batch Item - individual transfer within a batch.

    Links to expense claims and payment intents.
    """

    __tablename__ = "transfer_batch_item"
    __table_args__ = (
        Index("idx_transfer_batch_item_batch", "batch_id"),
        Index("idx_transfer_batch_item_claim", "expense_claim_id"),
        Index("idx_transfer_batch_item_intent", "payment_intent_id"),
        {"schema": "payments"},
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payments.transfer_batch.batch_id"),
        nullable=False,
    )

    # Sequence in batch
    sequence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    # Source document
    expense_claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("expense.expense_claim.claim_id"),
        nullable=False,
    )

    # Recipient details (denormalized for batch processing)
    recipient_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    recipient_bank_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    recipient_account_number: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    # Amount
    amount: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
    )
    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="NGN",
    )

    # Transfer details (after processing)
    transfer_recipient_code: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Paystack recipient code",
    )
    transfer_reference: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    transfer_code: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )

    # Payment intent linkage
    payment_intent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payments.payment_intent.intent_id"),
        nullable=True,
        comment="Created when transfer is initiated",
    )

    # Status
    status: Mapped[TransferBatchItemStatus] = mapped_column(
        Enum(
            TransferBatchItemStatus,
            name="transfer_batch_item_status",
            schema="payments",
            create_type=False,
        ),
        nullable=False,
        default=TransferBatchItemStatus.PENDING,
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
    )

    # Fee tracking
    fee_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(19, 4),
        nullable=True,
    )

    # Timestamps
    processed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    batch: Mapped["TransferBatch"] = relationship(
        "TransferBatch",
        back_populates="items",
    )
    payment_intent: Mapped[Optional["PaymentIntent"]] = relationship(
        "PaymentIntent",
        foreign_keys=[payment_intent_id],
    )

    def __repr__(self) -> str:
        return f"<TransferBatchItem {self.recipient_name}: {self.amount}>"
