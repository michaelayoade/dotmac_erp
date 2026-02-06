"""
Payment Intent Model - Payments Schema.

Tracks Paystack payment initialization and completion.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import DateTime, Enum, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PaymentIntentStatus(str, enum.Enum):
    """Status of a payment intent."""

    PENDING = "PENDING"  # Created, awaiting payment
    PROCESSING = "PROCESSING"  # Payment in progress
    COMPLETED = "COMPLETED"  # Successfully paid
    FAILED = "FAILED"  # Payment failed
    REVERSED = "REVERSED"  # Completed but later reversed/refunded
    ABANDONED = "ABANDONED"  # User didn't complete
    EXPIRED = "EXPIRED"  # Timed out


class PaymentDirection(str, enum.Enum):
    """Direction of payment flow."""

    INBOUND = "INBOUND"  # Collection - money coming in (customer payments)
    OUTBOUND = "OUTBOUND"  # Transfer - money going out (expense reimbursements)


class PaymentIntent(Base):
    """
    Payment Intent - tracks a payment from initialization to completion.

    A PaymentIntent is created when a user initiates a payment, and is updated
    as the payment progresses through the Paystack flow.
    """

    __tablename__ = "payment_intent"
    __table_args__ = {"schema": "payments"}

    intent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # Paystack reference - our unique reference sent to Paystack
    paystack_reference: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
    )
    # Access code returned by Paystack
    paystack_access_code: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    # URL to redirect user for payment
    authorization_url: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
    )

    # Payment details
    amount: Mapped[Decimal] = mapped_column(
        Numeric(19, 4),
        nullable=False,
        comment="Amount in currency units (e.g., Naira, not kobo)",
    )
    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="NGN",
    )
    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Customer email for Paystack",
    )

    # Payment direction - inbound (collection) or outbound (transfer)
    direction: Mapped[PaymentDirection] = mapped_column(
        Enum(
            PaymentDirection,
            name="payment_direction",
            schema="payments",
            create_type=False,
        ),
        nullable=False,
        default=PaymentDirection.INBOUND,
        comment="INBOUND for collections, OUTBOUND for transfers/payouts",
    )

    # Bank account linkage for reconciliation
    bank_account_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Bank account for settlement/source of funds",
    )

    # Source reference - what is being paid
    source_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        comment="INVOICE, EXPENSE_CLAIM, GENERAL",
    )
    source_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="ID of the document being paid",
    )

    # Transfer-specific fields (for OUTBOUND payments)
    transfer_recipient_code: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Paystack transfer recipient code for payouts",
    )
    transfer_code: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Paystack transfer code after initiation",
    )
    recipient_bank_code: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="Recipient bank code for transfers",
    )
    recipient_account_number: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="Recipient account number for transfers",
    )
    recipient_account_name: Mapped[Optional[str]] = mapped_column(
        String(200),
        nullable=True,
        comment="Recipient account name (verified by Paystack)",
    )

    # Status
    status: Mapped[PaymentIntentStatus] = mapped_column(
        Enum(
            PaymentIntentStatus,
            name="payment_intent_status",
            schema="payments",
            create_type=False,
        ),
        nullable=False,
        default=PaymentIntentStatus.PENDING,
    )

    # Result after completion
    customer_payment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Links to AR customer_payment after successful payment",
    )
    paystack_transaction_id: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Paystack transaction ID",
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    gateway_response: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
        comment="Full response from Paystack",
    )

    # Fee tracking
    fee_amount: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(19, 4),
        nullable=True,
        comment="Gateway fee charged (in currency units, not kobo)",
    )
    fee_journal_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="GL journal entry for fee posting",
    )

    # Custom data
    intent_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True,
        comment="Custom metadata (invoice_number, customer_name, etc.)",
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When this intent expires",
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
