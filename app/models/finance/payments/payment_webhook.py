"""
Payment Webhook Model - Payments Schema.

Audit log for incoming Paystack webhooks with idempotency.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class WebhookStatus(str, enum.Enum):
    """Processing status of a webhook."""

    RECEIVED = "RECEIVED"  # Just received, not yet processed
    PROCESSING = "PROCESSING"  # Currently being processed
    PROCESSED = "PROCESSED"  # Successfully processed
    FAILED = "FAILED"  # Processing failed
    DUPLICATE = "DUPLICATE"  # Duplicate webhook (idempotency)


class PaymentWebhook(Base):
    """
    Payment Webhook - audit log for incoming Paystack webhooks.

    Provides idempotency by tracking unique event IDs, and maintains
    an audit trail of all webhook events for debugging and compliance.
    """

    __tablename__ = "payment_webhook"
    __table_args__ = {"schema": "payments"}

    webhook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    # Resolved from payment intent after lookup
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Paystack event details
    event_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Paystack event type (charge.success, transfer.success, etc.)",
    )
    paystack_event_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        unique=True,
        comment="Unique event identifier for idempotency",
    )
    paystack_reference: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
        comment="Our reference from the payment",
    )

    # Payload
    payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Full webhook payload for audit",
    )
    signature: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        comment="X-Paystack-Signature header for audit",
    )

    # Processing status
    status: Mapped[WebhookStatus] = mapped_column(
        Enum(
            WebhookStatus,
            name="webhook_status",
            schema="payments",
            create_type=False,
        ),
        nullable=False,
        default=WebhookStatus.RECEIVED,
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Error message if processing failed",
    )
    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
