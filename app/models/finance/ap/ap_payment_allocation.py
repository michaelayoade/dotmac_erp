"""
AP Payment Allocation Model - AP Schema.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class APPaymentAllocation(Base):
    """
    Allocation of supplier payment to invoice.
    """

    __tablename__ = "payment_allocation"
    __table_args__ = (
        UniqueConstraint("payment_id", "invoice_id", name="uq_ap_allocation"),
        {"schema": "ap"},
    )

    allocation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ap.supplier_payment.payment_id"),
        nullable=False,
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ap.supplier_invoice.invoice_id"),
        nullable=False,
    )

    allocated_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    discount_taken: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=0
    )
    exchange_difference: Mapped[Decimal] = mapped_column(
        Numeric(20, 6),
        nullable=False,
        default=0,
    )

    allocation_date: Mapped[date] = mapped_column(Date, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
