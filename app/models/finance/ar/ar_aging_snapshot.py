"""
AR Aging Snapshot Model - AR Schema.
Point-in-time aging for audit evidence.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ARAgingSnapshot(Base):
    """
    Point-in-time AR aging snapshot.
    """

    __tablename__ = "ar_aging_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "fiscal_period_id",
            "customer_id",
            "aging_bucket",
            name="uq_ar_aging",
        ),
        {"schema": "ar"},
    )

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ar.customer.customer_id"),
        nullable=False,
    )
    aging_bucket: Mapped[str] = mapped_column(String(30), nullable=False)

    amount_functional: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    invoice_count: Mapped[int] = mapped_column(Integer, nullable=False)

    currency_code: Mapped[Optional[str]] = mapped_column(String(3), nullable=True)
    amount_original_currency: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
