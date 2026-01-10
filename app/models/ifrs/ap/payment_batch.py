"""
AP Payment Batch Model - AP Schema.
"""
import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class APBatchStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class APPaymentBatch(Base):
    """
    Payment batch for bulk supplier payments.
    """

    __tablename__ = "payment_batch"
    __table_args__ = (
        UniqueConstraint("organization_id", "batch_number", name="uq_payment_batch"),
        {"schema": "ap"},
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
    )

    batch_number: Mapped[str] = mapped_column(String(30), nullable=False)
    batch_date: Mapped[date] = mapped_column(Date, nullable=False)
    payment_method: Mapped[str] = mapped_column(String(30), nullable=False)

    bank_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)

    total_payments: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=0)

    status: Mapped[APBatchStatus] = mapped_column(
        Enum(APBatchStatus, name="ap_batch_status"),
        nullable=False,
        default=APBatchStatus.DRAFT,
    )

    # Bank file generation
    bank_file_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    bank_file_reference: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    bank_file_generated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

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

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
