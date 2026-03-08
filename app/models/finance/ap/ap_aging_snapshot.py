"""
AP Aging Snapshot Model - AP Schema.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class APAgingSnapshot(Base):
    """
    Point-in-time AP aging snapshot.
    """

    __tablename__ = "ap_aging_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "fiscal_period_id",
            "supplier_id",
            "aging_bucket",
            name="uq_ap_aging",
        ),
        {"schema": "ap"},
    )

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
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
    fiscal_period_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("gl.fiscal_period.fiscal_period_id"),
        nullable=False,
    )
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)

    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ap.supplier.supplier_id"),
        nullable=False,
    )
    aging_bucket: Mapped[str] = mapped_column(String(30), nullable=False)

    amount_functional: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    invoice_count: Mapped[int] = mapped_column(Integer, nullable=False)

    currency_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    amount_original_currency: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 6),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
