"""
Supplier Payment Model - AP Schema.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class APPaymentMethod(str, enum.Enum):
    CHECK = "CHECK"
    BANK_TRANSFER = "BANK_TRANSFER"
    WIRE = "WIRE"
    ACH = "ACH"
    CARD = "CARD"


class APPaymentStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    SENT = "SENT"
    CLEARED = "CLEARED"
    VOID = "VOID"
    REJECTED = "REJECTED"


class SupplierPayment(Base):
    """
    Supplier payment.
    """

    __tablename__ = "supplier_payment"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "payment_number", name="uq_supplier_payment"
        ),
        Index("idx_supplier_payment_supplier", "supplier_id"),
        {"schema": "ap"},
    )

    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ap.supplier.supplier_id"),
        nullable=False,
    )

    payment_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    payment_number: Mapped[str] = mapped_column(String(30), nullable=False)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)

    payment_method: Mapped[APPaymentMethod] = mapped_column(
        Enum(APPaymentMethod, name="ap_payment_method"),
        nullable=False,
    )

    # Amounts
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    exchange_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 10), nullable=True
    )
    functional_currency_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
    )

    # Bank
    bank_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Status
    status: Mapped[APPaymentStatus] = mapped_column(
        Enum(APPaymentStatus, name="ap_payment_status"),
        nullable=False,
        default=APPaymentStatus.DRAFT,
    )

    # Accounting
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    posting_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    bank_reconciliation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Withholding Tax
    withholding_tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )
    withholding_tax_code_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tax.tax_code.tax_code_id"),
        nullable=True,
    )
    # Gross amount = amount (net paid) + withholding_tax_amount
    gross_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )

    # Remittance
    remittance_advice_sent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    remittance_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # SoD tracking
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    posted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    approval_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    correlation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=func.now(),
    )
